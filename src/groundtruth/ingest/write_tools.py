"""Write tools: ``create_note`` and ``update_note`` (spec §7.6, invariant 2, ADR-5).

The only way LLM output reaches disk. The model never emits a filesystem path —
it supplies a folder and a title and the system derives the path. **Nothing is
written here**: the tools buffer intentions into :class:`PendingWrites` for the
validator (#18) to gate and the committer to apply, which is what makes
all-or-nothing (§7.7) achievable.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from ..models import NoteFrontmatter
from ..storage.paths import UnsafePathError, resolve_in_vault, sanitize_title

# --- argument normalisation ----------------------------------------------------
# A local model, told "supply the folder, the title, and the body", still tends
# to pass a *filename* as the title and to prepend its own YAML frontmatter to
# the body. Neither is a content decision, so we normalise the tool arguments
# here rather than fail the job (ADR-5 is about not silently sanitising the
# model's *content*, not its packaging).

_TITLE_EXT = re.compile(r"\.(?:md|markdown|txt)$", re.IGNORECASE)
_FM_KEYS = "tags|title|sources|created|updated|aliases"
_LEADING_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL)
_FRONTMATTER_KEY = re.compile(rf"(?im)^[ \t]*(?:{_FM_KEYS})[ \t]*:")
#: A leading run of frontmatter-shaped lines the model wrote *without* the ``---``
#: fences: one or more ``key:`` / ``- item`` / blank lines, starting with a known
#: frontmatter key, then a blank line before the real body.
_FM_LINE = rf"[ \t]*(?:{_FM_KEYS})[ \t]*:.*\r?\n"  # a "key: ..." line
_UNFENCED_FRONTMATTER = re.compile(
    rf"\A{_FM_LINE}"  # first line is a known frontmatter key
    rf"(?:{_FM_LINE}|[ \t]*-[ \t].*\r?\n|[ \t]*\r?\n)*"  # then more keys / list items / blanks
    rf"\r?\n",  # a blank line closes the block
    re.IGNORECASE,
)


def _normalize_title(title: Any) -> Any:
    """Drop a note-file extension the model tacked onto a *title* (``Foo.md`` →
    ``Foo``). Keeps ``create_note("x", "Foo")`` and ``create_note("x", "Foo.md")``
    pointing at the same path so the validator's duplicate check can see them.
    """
    return _TITLE_EXT.sub("", title).strip() if isinstance(title, str) else title


def _strip_body_frontmatter(body: Any) -> Any:
    """Remove a leading frontmatter block the model prepended to the body — both
    the ``---`` … ``---`` fenced form and the bare ``tags:`` / ``sources:`` … run
    it writes with no fences. The system owns frontmatter (tags/sources/
    timestamps); only strip a block that actually starts with a frontmatter key,
    never a stray ``---`` rule or ordinary prose.
    """
    if not isinstance(body, str):
        return body
    fenced = _LEADING_FRONTMATTER.match(body)
    if fenced and _FRONTMATTER_KEY.search(fenced.group(1)):
        return body[fenced.end() :].lstrip("\n")
    unfenced = _UNFENCED_FRONTMATTER.match(body)
    if unfenced:
        return body[unfenced.end() :].lstrip("\n")
    return body


@dataclass(frozen=True)
class PendingNote:
    """A buffered create or update.

    Frontmatter is built by the system (never the model) and attached before
    validation — ``frontmatter`` is ``None`` while the note is still being drafted.
    """

    path: str
    body: str
    is_new: bool
    folder: str | None = None
    title: str | None = None
    frontmatter: NoteFrontmatter | Mapping[str, Any] | None = None

    def with_frontmatter(self, frontmatter: NoteFrontmatter | Mapping[str, Any]) -> PendingNote:
        return PendingNote(
            path=self.path,
            body=self.body,
            is_new=self.is_new,
            folder=self.folder,
            title=self.title,
            frontmatter=frontmatter,
        )


@dataclass
class PendingWrites:
    """The buffered writes for one job, consumed by the validator and committer."""

    notes: list[PendingNote] = field(default_factory=list)

    def __iter__(self) -> Iterator[PendingNote]:
        return iter(self.notes)

    def __len__(self) -> int:
        return len(self.notes)

    @property
    def paths(self) -> list[str]:
        return [note.path for note in self.notes]


class WriteTools:
    """LLM-callable buffered write tools for one ingest job."""

    def __init__(self, vault_root: Path | str, existing_paths: Collection[str]) -> None:
        self._root = Path(vault_root).resolve()
        self._existing = set(existing_paths)
        self.pending = PendingWrites()

    def _rel(self, *parts: str) -> str:
        resolved = resolve_in_vault(self._root, *parts)
        return resolved.relative_to(self._root).as_posix()

    def create_note(self, folder: str, title: str, body: str) -> str:
        title = _normalize_title(title)
        body = _strip_body_frontmatter(body)
        try:
            rel = self._rel(folder, f"{sanitize_title(title)}.md")
        except UnsafePathError as exc:
            return f"error: {exc}"
        if rel in set(self.pending.paths):
            return f"error: a note at {rel!r} was already staged in this job"
        self.pending.notes.append(
            PendingNote(path=rel, body=body, is_new=True, folder=folder, title=title)
        )
        return f"created {rel}"

    def update_note(self, path: str, body: str) -> str:
        body = _strip_body_frontmatter(body)
        try:
            rel = self._rel(path)
        except UnsafePathError as exc:
            return f"error: {exc}"
        if rel not in self._existing:
            return f"error: no note exists at {rel!r}; use create_note"
        if rel in set(self.pending.paths):
            return f"error: {rel!r} was already staged in this job"
        self.pending.notes.append(PendingNote(path=rel, body=body, is_new=False))
        return f"updated {rel}"

    def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "create_note":
            return self.create_note(arguments["folder"], arguments["title"], arguments["body"])
        if name == "update_note":
            return self.update_note(arguments["path"], arguments["body"])
        return f"unknown tool: {name}"

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return self.TOOL_SCHEMAS

    TOOL_SCHEMAS: ClassVar[list[dict[str, Any]]] = [
        {
            "type": "function",
            "function": {
                "name": "create_note",
                "description": (
                    "Create a new note. Supply the destination folder and a title; "
                    "the system derives the filename. Buffered until the job commits."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "folder": {"type": "string", "description": "A folder from schema.md."},
                        "title": {"type": "string", "description": "The note title."},
                        "body": {
                            "type": "string",
                            "description": "Markdown body with [[wikilinks]].",
                        },
                    },
                    "required": ["folder", "title", "body"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "update_note",
                "description": "Replace the body of an existing note. Buffered until commit.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path of an existing note."},
                        "body": {"type": "string", "description": "The new Markdown body."},
                    },
                    "required": ["path", "body"],
                },
            },
        },
    ]


__all__ = ["PendingNote", "PendingWrites", "WriteTools"]
