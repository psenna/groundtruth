"""Filename sanitization and vault path containment (spec §7.6, invariant 2).

Security-critical. The LLM supplies note titles and folder names; nothing derived
from model output may resolve outside the vault directory. Per ADR-5 this module
**rejects** unsafe input — it never repairs it and never returns a fallback path.

Containment is enforced by walking the path one component at a time and refusing
to cross a symlink, rather than trusting ``Path.resolve()`` — on CPython <= 3.12
a non-strict ``resolve()`` gives up on symlink loops and collapses ``..``
lexically, which is a containment bypass.

This module guards *path* safety only. The write side (``NoteRepository``) must
still open with ``O_NOFOLLOW`` and reject multi-linked inodes to close hardlink
and check-to-write races, and the write validator (§7.6) enforces the
schema.md folder allowlist and the schema.md write ban (invariant 1).
"""

from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path, PurePosixPath
from typing import NoReturn

from ..errors import WriteValidationError

#: Longest filename stem we emit, in bytes, leaving room for ``.md`` and a git path.
_MAX_STEM_BYTES = 200
_MAX_SEGMENT_BYTES = 255
_MAX_DEPTH = 64
#: Characters never allowed in a title (path separators + Windows-forbidden set).
_FORBIDDEN_CHARS = frozenset('<>:"/\\|?*\x00')
#: Same set but keeping ``/`` — it is the segment separator inside a path part.
_PART_FORBIDDEN_CHARS = _FORBIDDEN_CHARS - {"/"}
_CONTROL_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})
#: Windows reserved device names (checked case-insensitively, with or without extension).
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$", "clock$"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


class UnsafePathError(WriteValidationError):
    """Input derived from model output would escape the vault or is not a safe filename."""


def _reject(message: str) -> NoReturn:
    raise UnsafePathError(message, stage="write-validation")


def _reserved_stem(text: str) -> str:
    return text.split(".", 1)[0].rstrip(" ").lower()


def sanitize_title(title: str) -> str:
    """Return a filesystem-safe filename stem for ``title``, or raise ``UnsafePathError``.

    Unicode is preserved where safe and NFC-normalized. Overlong titles are
    truncated deterministically with a hash suffix so distinct titles never
    collide.
    """
    if not isinstance(title, str):
        _reject("title must be a string")

    normalized = unicodedata.normalize("NFC", title)

    if normalized != normalized.strip():
        _reject(f"title {title!r} has leading or trailing whitespace")
    if not normalized:
        _reject("title is empty")
    if normalized.startswith("~"):
        _reject(f"title {title!r} starts with '~'")
    if normalized.startswith("."):
        _reject(f"title {title!r} starts with '.'")
    if normalized.endswith((".", " ")):
        _reject(f"title {title!r} ends with a dot or space")

    # Check the NFC form and its NFKC compatibility form: a downstream NFKC pass
    # (slugging, indexing) must not be able to manufacture a separator or "..".
    nfkc = unicodedata.normalize("NFKC", normalized)
    for text in (normalized, nfkc):
        if ".." in text:
            _reject(f"title {title!r} contains '..'")
        for ch in text:
            if ch in _FORBIDDEN_CHARS:
                _reject(f"title {title!r} contains (or normalizes to) a forbidden character {ch!r}")
            if unicodedata.category(ch) in _CONTROL_CATEGORIES:
                _reject(f"title {title!r} contains a control or unassigned character")

    if _reserved_stem(normalized) in _WINDOWS_RESERVED or _reserved_stem(nfkc) in _WINDOWS_RESERVED:
        _reject(f"title {title!r} is a reserved device name")

    if len(normalized.encode()) > _MAX_STEM_BYTES:
        digest = hashlib.sha256(normalized.encode()).hexdigest()[:12]
        budget = _MAX_STEM_BYTES - len(digest) - 1
        truncated = normalized.encode()[:budget].decode("utf-8", "ignore").rstrip(". ")
        if not truncated:
            _reject(f"title {title!r} has no safe content to keep after truncation")
        normalized = f"{truncated}-{digest}"

    return normalized


def _validate_segment(part: str, seg: str) -> None:
    if seg == "":
        _reject(f"path part {part!r} has an empty segment")
    if seg in (".", ".."):
        _reject(f"path part {part!r} contains a {seg!r} segment")
    if seg.startswith("."):
        _reject(f"path segment {seg!r} starts with '.'")
    if len(seg.encode()) > _MAX_SEGMENT_BYTES:
        _reject(f"path segment {seg!r} is too long")
    nfkc = unicodedata.normalize("NFKC", seg)
    if ".." in nfkc or any(ch in _FORBIDDEN_CHARS for ch in nfkc):
        _reject(f"path segment {seg!r} normalizes to something unsafe")


def resolve_in_vault(vault_root: Path | str, *parts: str) -> Path:
    """Resolve ``parts`` under ``vault_root`` and prove the result stays inside it.

    Every component is checked as the path is built: a component that is a symlink
    is rejected outright, so a symlinked folder pointing outside the vault (or a
    symlink loop) cannot be used to escape. Any violation raises ``UnsafePathError``.
    """
    root = Path(vault_root).resolve()

    if not parts:
        _reject("no path parts given")

    segments: list[str] = []
    for part in parts:
        if not isinstance(part, str) or part == "":
            _reject("path part must be a non-empty string")
        if part.startswith("~"):
            _reject(f"path part {part!r} starts with '~'")
        for ch in part:
            if ch in _PART_FORBIDDEN_CHARS:
                _reject(f"path part {part!r} contains a forbidden character {ch!r}")
            if unicodedata.category(ch) in _CONTROL_CATEGORIES:
                _reject(f"path part {part!r} contains a control or unassigned character")
        if PurePosixPath(part).is_absolute():
            _reject(f"path part {part!r} is absolute")
        for seg in part.split("/"):
            _validate_segment(part, seg)
            segments.append(seg)

    if not segments:
        _reject("no usable path segments")
    if len(segments) > _MAX_DEPTH:
        _reject(f"path nests deeper than {_MAX_DEPTH} segments")

    current = root
    for seg in segments:
        current = current / seg
        if current.is_symlink():
            _reject(f"path component {seg!r} is a symlink")

    try:
        real = current.resolve()
    except OSError as exc:
        _reject(f"path could not be resolved: {exc}")
    if real != root and root not in real.parents:
        _reject(f"resolved path {real} escapes vault {root}")
    return current


__all__ = ["UnsafePathError", "resolve_in_vault", "sanitize_title"]
