"""Write tools: ``create_note`` and ``update_note`` (spec §7.6, invariant 2, ADR-5).

The only way LLM output reaches disk. The model never emits a filesystem path —
it supplies a folder and a title and the system derives the path. **Nothing is
written here**: the tools buffer intentions into :class:`PendingWrites` for the
validator (#18) to gate and the committer to apply, which is what makes
all-or-nothing (§7.7) achievable.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from ..storage.paths import UnsafePathError, resolve_in_vault, sanitize_title


@dataclass(frozen=True)
class PendingNote:
    """A buffered create or update. Frontmatter is built later by the system, not the model."""

    path: str
    body: str
    is_new: bool
    folder: str | None = None
    title: str | None = None


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
