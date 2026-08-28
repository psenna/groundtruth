"""The filesystem layer for notes (spec §5, §6).

Every read and write of a note goes through here, so vault containment (#7) has
exactly one enforcement point. Writes use ``O_NOFOLLOW`` and reject multi-linked
inodes, closing the hardlink / symlink-swap gaps that path-string checks cannot.
"""

from __future__ import annotations

import os
import stat
from datetime import date
from pathlib import Path

from ..errors import GroundtruthError
from ..models import Note, NoteFrontmatter
from .frontmatter import parse_note, render_note
from .paths import UnsafePathError, resolve_in_vault

_SCHEMA_FILENAME = "schema.md"


class NoteRepositoryError(GroundtruthError):
    """A note operation failed."""


class NoteNotFoundError(NoteRepositoryError):
    """No note exists at the requested vault-relative path."""


def _dedupe(hashes: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for h in hashes:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


class NoteRepository:
    """Read, write and list notes for a single vault."""

    def __init__(self, vault_root: Path | str) -> None:
        self.root = Path(vault_root).resolve()

    def _resolve(self, path: str) -> Path:
        return resolve_in_vault(self.root, path)

    def read(self, path: str) -> Note:
        target = self._resolve(path)
        if target.is_symlink() or not target.is_file():
            raise NoteNotFoundError(f"{path}: no such note in vault {self.root}")
        return parse_note(target.read_text(encoding="utf-8"), path=path)

    def exists(self, path: str) -> bool:
        try:
            target = self._resolve(path)
        except UnsafePathError:
            return False
        return target.is_file() and not target.is_symlink()

    def write(self, note: Note) -> Note:
        """Persist ``note``. ``updated`` is set to today; ``created`` and prior
        ``sources`` are preserved from disk; ``sources`` is append-only and
        de-duplicated. Returns the note as persisted.
        """
        target = self._resolve(note.path)

        prior_sources: list[str] = []
        created = note.frontmatter.created
        if target.is_file() and not target.is_symlink():
            existing = parse_note(target.read_text(encoding="utf-8"), path=note.path)
            created = existing.frontmatter.created
            prior_sources = existing.frontmatter.sources

        persisted = Note(
            path=note.path,
            frontmatter=NoteFrontmatter(
                title=note.frontmatter.title,
                tags=note.frontmatter.tags,
                sources=_dedupe([*prior_sources, *note.frontmatter.sources]),
                created=created,
                updated=date.today(),
            ),
            body=note.body,
        )

        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target, render_note(persisted))
        return persisted

    def list_notes(self, *, tag: str | None = None) -> list[Note]:
        notes: list[Note] = []
        for path in sorted(self.root.rglob("*.md")):
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(self.root).as_posix()
            if Path(rel).name == _SCHEMA_FILENAME:
                continue
            try:
                target = resolve_in_vault(self.root, rel)
            except UnsafePathError:
                continue
            note = parse_note(target.read_text(encoding="utf-8"), path=rel)
            if tag is None or tag in note.frontmatter.tags:
                notes.append(note)
        return notes

    @staticmethod
    def _atomic_write(target: Path, text: str) -> None:
        if target.is_symlink():
            raise UnsafePathError(f"{target} is a symlink", stage="write-validation")
        if target.exists():
            st = target.lstat()
            if stat.S_ISLNK(st.st_mode) or st.st_nlink > 1:
                raise UnsafePathError(
                    f"{target} is a symlink or a hardlink to another inode",
                    stage="write-validation",
                )

        tmp = target.parent / f"{target.name}.{os.getpid()}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
        fd = os.open(tmp, flags, 0o644)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            tmp.replace(target)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise


__all__ = ["NoteNotFoundError", "NoteRepository", "NoteRepositoryError"]
