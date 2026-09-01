"""Write validator: the gate between model output and the repository (spec §7.6).

**Any violation fails the whole job with nothing staged.** There is no repair and
no normalize-and-continue — ADR-5 rejected sanitize-and-continue because it erodes
vault quality silently. Every check corresponds to a row of the §7.6 table (plus
the invariant-1 ban on writing ``schema.md``), and the first failure aborts the
batch. Every rejection is a ``ValidationRejectionError`` (a ``TerminalError``)
that names the rule and the offending note.

The validator keys **every** check on ``note.path`` — the string the committer
actually writes — not on the model-supplied ``folder``/``title`` fields, which it
re-derives and cross-checks.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from typing import Any, NoReturn

import yaml
from pydantic import ValidationError

from ..config import Limits
from ..errors import WriteValidationError
from ..models import Note, NoteFrontmatter
from ..storage.frontmatter import render_note
from ..storage.paths import UnsafePathError, resolve_in_vault, sanitize_title
from .links import check_links, extract_links
from .schema import Schema
from .write_tools import PendingNote, PendingWrites

_STAGE = "write-validation"
_SCHEMA_FILENAME = "schema.md"
#: Top-level names a note may never be written under.
_NON_NOTE_ROOTS = frozenset({"external", ".git"})


class ValidationRejectionError(WriteValidationError):
    """A staged change violated a §7.6 rule. Names the rule and the offending note."""

    def __init__(self, rule: str, note_path: str, detail: str) -> None:
        super().__init__(f"{rule} check failed for {note_path!r}: {detail}", stage=_STAGE)
        self.rule = rule
        self.note_path = note_path


def _reject(rule: str, note_path: str, detail: str) -> NoReturn:
    raise ValidationRejectionError(rule, note_path, detail)


# --- per-batch --------------------------------------------------------------------------


def _check_batch(pending: PendingWrites, limits: Limits) -> None:
    if len(pending) == 0:
        _reject("empty_batch", "<batch>", "an ingest must write at least one note (§7.7)")
    if len(pending) > limits.max_notes_per_ingest:
        _reject(
            "note_count",
            "<batch>",
            f"{len(pending)} notes touched, limit is {limits.max_notes_per_ingest}",
        )
    seen: set[str] = set()
    for path in pending.paths:
        if path in seen:
            _reject("duplicate_path", path, "the same path is staged more than once")
        seen.add(path)


# --- per-note --------------------------------------------------------------------------


def _check_body_type(note: PendingNote) -> None:
    if not isinstance(note.body, str):
        _reject("body_type", note.path, f"body must be a string, got {type(note.body).__name__}")


#: A body that says nothing — the model created a shell it meant to fill later.
_PLACEHOLDER = re.compile(
    r"\A[\W_]*(?:placeholder|to-?do|tbd|tba|wip|fixme|stub|"
    r"coming soon|to be (?:written|added|filled|completed|done))[\W_]*\Z",
    re.IGNORECASE,
)


def _check_body_substance(note: PendingNote) -> None:
    """Reject a note whose body carries no real content — only headings, only
    ``[[wikilinks]]``, or a bare 'placeholder' / 'TODO'. A stub note is worse
    than no note; the retry (#94) then asks the model to write it or drop it.

    Runs after ``_check_body_type``, so ``note.body`` is known to be a ``str``.
    """
    prose_lines: list[str] = []
    for raw in note.body.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        prose_lines.append(re.sub(r"^[-*+]\s+", "", stripped))
    prose = " ".join(prose_lines).strip()
    if _PLACEHOLDER.match(prose):
        _reject("note_substance", note.path, f"the note body is a placeholder ({prose!r})")
    # ``[^\W_]`` is a letter or digit — a body with none, once headings and
    # ``[[links]]`` are removed, is only a heading, a link list, or punctuation.
    if not re.search(r"[^\W_]", re.sub(r"\[\[[^\]]*\]\]", "", prose)):
        _reject(
            "note_substance",
            note.path,
            "the note body has no content — only a heading or a link. Write the "
            "note in full or do not create it.",
        )


def _check_containment(note: PendingNote, vault_root: str) -> None:
    try:
        resolve_in_vault(vault_root, note.path)
    except UnsafePathError as exc:
        _reject("containment", note.path, str(exc))


def _check_path_shape(note: PendingNote) -> None:
    name = note.path.rsplit("/", 1)[-1]
    if not name.endswith(".md"):
        _reject("path", note.path, "note paths must end in .md")
    if name == _SCHEMA_FILENAME:
        _reject("path", note.path, "schema.md is never written by ingestion (invariant 1, ADR-12)")
    first = note.path.split("/", 1)[0]
    if first in _NON_NOTE_ROOTS or (first.startswith(".") and first != note.path):
        _reject("path", note.path, f"{first!r} is not a writable location for a note")

    stem = name[:-3]
    try:
        safe = sanitize_title(stem)
    except UnsafePathError as exc:
        _reject("filename", note.path, str(exc))
    if safe != stem:
        _reject("filename", note.path, f"filename stem {stem!r} is not in sanitized form")


def _check_create_derivation(note: PendingNote, schema: Schema) -> None:
    if note.folder is None or note.title is None:
        _reject("filename", note.path, "a create must carry both folder and title")
    if note.folder not in schema.folders:
        _reject("folder", note.path, f"folder {note.folder!r} is not declared in schema.md")
    try:
        expected_name = f"{sanitize_title(note.title)}.md"
    except UnsafePathError as exc:
        _reject("filename", note.path, str(exc))
    expected = f"{note.folder.strip('/')}/{expected_name}" if note.folder else expected_name
    if note.path != expected:
        _reject(
            "filename",
            note.path,
            f"path does not match folder/title (expected {expected!r})",
        )


def _coerce_frontmatter(note: PendingNote) -> NoteFrontmatter:
    fm: Any = note.frontmatter
    if fm is None:
        _reject("frontmatter", note.path, "no frontmatter attached")
    if isinstance(fm, str):
        try:
            fm = yaml.safe_load(fm)
        except yaml.YAMLError as exc:
            _reject("frontmatter", note.path, f"unparseable frontmatter YAML: {exc}")
    if isinstance(fm, NoteFrontmatter):
        data = dict(fm)  # raw field values; re-validated below (model_construct bypasses it)
    elif isinstance(fm, Mapping):
        data = dict(fm)
    else:
        _reject("frontmatter", note.path, f"unusable frontmatter of type {type(fm).__name__}")
    try:
        return NoteFrontmatter.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        _reject("frontmatter", note.path, f"invalid frontmatter: {problems}")


def _check_note_size(note: PendingNote, frontmatter: NoteFrontmatter, limits: Limits) -> None:
    rendered = render_note(Note(path=note.path, frontmatter=frontmatter, body=note.body))
    size = len(rendered.encode("utf-8"))
    if size > limits.max_note_bytes:
        _reject(
            "note_size",
            note.path,
            f"rendered note is {size} bytes, limit is {limits.max_note_bytes}",
        )


def _check_links(note: PendingNote, existing: Collection[str], created: Collection[str]) -> None:
    dangling = check_links(extract_links(note.body), existing, created)
    if dangling:
        targets = ", ".join(sorted({d.target for d in dangling}))
        _reject("link_integrity", note.path, f"dangling wikilink(s): {targets}")


def _check_existence(note: PendingNote, existing: Collection[str]) -> None:
    if note.is_new and note.path in existing:
        _reject("collision", note.path, "create targets a note that already exists")
    if not note.is_new and note.path not in existing:
        _reject("missing_target", note.path, "update targets a note that does not exist")


def validate(
    pending: PendingWrites,
    schema: Schema,
    limits: Limits,
    *,
    vault_root: str,
    existing_paths: Collection[str] = (),
) -> None:
    """Validate a batch of staged writes. Returns ``None`` or raises ``ValidationRejectionError``.

    Limits come from config (#4). Nothing is staged if any check fails.
    """
    existing = set(existing_paths)
    _check_batch(pending, limits)

    created_this_batch = set(pending.paths)
    for note in pending:
        _check_body_type(note)
        _check_body_substance(note)
        _check_containment(note, str(vault_root))
        _check_path_shape(note)
        _check_existence(note, existing)
        if note.is_new:
            _check_create_derivation(note, schema)
        frontmatter = _coerce_frontmatter(note)
        _check_note_size(note, frontmatter, limits)
        _check_links(note, existing, created_this_batch)


__all__ = ["ValidationRejectionError", "validate"]
