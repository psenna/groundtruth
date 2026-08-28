"""Parse and render note frontmatter (spec §6).

Frontmatter *is* the retrieval index format, so rendering must be **stable**:
the same model always produces byte-identical output. Unstable rendering makes
every ingest a noisy git diff — treat it as a bug.
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import ValidationError

from ..errors import TerminalError
from ..models import Note, NoteFrontmatter

_DELIM = "---"
#: Fixed key order in rendered frontmatter (spec §6).
_KEY_ORDER = ("title", "tags", "sources", "created", "updated")


class FrontmatterError(TerminalError):
    """Base for frontmatter parse failures. Terminal — the same text fails identically."""


class MissingFrontmatterError(FrontmatterError):
    """The note has no ``---`` frontmatter block, or no closing delimiter."""


class MalformedFrontmatterError(FrontmatterError):
    """The frontmatter block is not a valid YAML mapping of the required shape."""


def _dump_scalar_line(key: str, value: Any) -> str:
    """Render ``key: value`` via YAML so strings/unicode are quoted correctly."""
    dumped = yaml.safe_dump(
        {key: value},
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=4096,
    )
    return dumped.strip()


def render_note(note: Note) -> str:
    """Render a note to frontmatter + body text. Deterministic (spec §6)."""
    fm = note.frontmatter
    lines = [
        _DELIM,
        _dump_scalar_line("title", fm.title),
        f"tags: [{', '.join(fm.tags)}]",
    ]
    if fm.sources:
        lines.append("sources:")
        lines.extend(f"  - {sha}" for sha in fm.sources)
    else:
        lines.append("sources: []")
    lines.append(_dump_scalar_line("created", fm.created))
    lines.append(_dump_scalar_line("updated", fm.updated))
    lines.append(_DELIM)
    lines.append("")
    return "\n".join(lines) + "\n" + note.body


def parse_note(text: str, path: str = "") -> Note:
    """Parse frontmatter + body into a :class:`Note`.

    ``path`` is stored on the note and named in error messages.
    """
    where = path or "<note>"
    lines = text.split("\n")
    if not lines or lines[0].strip() != _DELIM:
        raise MissingFrontmatterError(
            f"{where}: expected a '---' frontmatter block on the first line"
        )

    closing = next((i for i in range(1, len(lines)) if lines[i].strip() == _DELIM), None)
    if closing is None:
        raise MissingFrontmatterError(f"{where}: no closing '---' delimiter for the frontmatter")

    yaml_text = "\n".join(lines[1:closing])
    try:
        loaded = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise MalformedFrontmatterError(f"{where}: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(loaded, dict):
        raise MalformedFrontmatterError(f"{where}: frontmatter is not a YAML mapping")

    body = "\n".join(lines[closing + 1 :])
    if body.startswith("\n"):
        body = body[1:]  # drop the single blank line between frontmatter and body

    try:
        frontmatter = NoteFrontmatter.model_validate(loaded)
    except ValidationError as exc:
        raise MalformedFrontmatterError(f"{where}: {exc}") from exc

    return Note(path=path, frontmatter=frontmatter, body=body)


__all__ = [
    "FrontmatterError",
    "MalformedFrontmatterError",
    "MissingFrontmatterError",
    "parse_note",
    "render_note",
]
