"""``schema.md`` parsing (spec §5.2, §13.1) and the guarded write helper (ADR-12).

``schema.md`` is a **human document**: the user's folder list and prescriptive
tag guidance. Parsing tolerates prose, extra sections and reordering.

The ingestion pipeline never writes ``schema.md`` (ADR-12, invariant 1). The only
writer is :func:`write_schema`, gated by ``allowed`` and called **only** by the
MCP ``update_schema`` tool (#36). There is no append/merge/splice path here — a
derived tag list on disk would reintroduce the drift ADR-12 removed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..errors import TerminalError

_SCHEMA_FILENAME = "schema.md"
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_LIST_ITEM = re.compile(r"^\s*[-*+]\s+(.*)$")


class SchemaError(TerminalError):
    """``schema.md`` is malformed."""


class SchemaNotFoundError(SchemaError):
    """The vault has no ``schema.md``."""


class SchemaWriteRefusedError(TerminalError):
    """A ``schema.md`` write was attempted while ``allow_schema_writes`` is false."""


@dataclass(frozen=True)
class Schema:
    """The machine-readable view of a vault's ``schema.md``."""

    folders: list[str]
    #: The verbatim prescriptive tag guidance (the body of the Tags section).
    tag_guidance: str
    raw: str


def _sections(text: str) -> dict[str, list[str]]:
    """Split markdown into ``heading-title -> content lines`` (case-folded keys)."""
    sections: dict[str, list[str]] = {"": []}
    current = ""
    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading:
            current = heading.group(1).strip().casefold()
            sections.setdefault(current, [])
        else:
            sections[current].append(line)
    return sections


def _folder_name(item: str) -> str | None:
    # "companies/ - organizations" -> "companies"; "projects/" -> "projects"
    token = re.split(r"[\s\u2013\u2014:]", item.strip(), maxsplit=1)[0]
    token = token.strip().strip("`").rstrip("/")
    return token or None


def parse_schema(text: str) -> Schema:
    """Parse ``schema.md`` content. Raises :class:`SchemaError` if it is malformed."""
    without_comments = _HTML_COMMENT.sub("", text)
    sections = _sections(without_comments)

    if "folders" not in sections:
        raise SchemaError("schema.md has no '## Folders' section")

    folders: list[str] = []
    for line in sections["folders"]:
        item = _LIST_ITEM.match(line)
        if not item:
            continue
        name = _folder_name(item.group(1))
        if name and name not in folders:
            folders.append(name)
    if not folders:
        raise SchemaError("schema.md '## Folders' section lists no folders")

    tag_guidance = "\n".join(sections.get("tags", [])).strip()
    return Schema(folders=folders, tag_guidance=tag_guidance, raw=text)


def load_schema(vault_dir: Path | str) -> Schema:
    """Read and parse ``<vault_dir>/schema.md``."""
    path = Path(vault_dir) / _SCHEMA_FILENAME
    if not path.is_file():
        raise SchemaNotFoundError(f"{path}: no schema.md in this vault")
    return parse_schema(path.read_text(encoding="utf-8"))


def write_schema(vault_dir: Path | str, markdown: str, *, allowed: bool) -> None:
    """Overwrite ``schema.md`` with ``markdown`` exactly as given (MCP ``update_schema`` only).

    Refuses unless ``allowed`` (``allow_schema_writes``, default false). The caller
    supplies the full content; this function never merges or splices.
    """
    if not allowed:
        raise SchemaWriteRefusedError(
            "schema.md write refused: allow_schema_writes is false (§5.2, ADR-12)"
        )
    path = Path(vault_dir) / _SCHEMA_FILENAME
    tmp = path.parent / f"{_SCHEMA_FILENAME}.{os.getpid()}.tmp"
    tmp.write_text(markdown, encoding="utf-8")
    try:
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


__all__ = [
    "Schema",
    "SchemaError",
    "SchemaNotFoundError",
    "SchemaWriteRefusedError",
    "load_schema",
    "parse_schema",
    "write_schema",
]
