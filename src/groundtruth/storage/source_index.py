"""Per-vault source index in the state dir (spec §5.1, ADR-7).

Maps source SHA-256 -> job id, commit sha, notes touched, ingested_at. It lives
in ``<state-dir>/index/<vault>.json`` — **never** in a vault repo — so dedup
(§7.2) and note provenance keep working when ``raw_archive`` is disabled.
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..models import SourceRecord


class SourceIndex:
    """Reads and writes the source index for any vault under one state dir."""

    def __init__(self, state_dir: Path | str) -> None:
        self._dir = Path(state_dir) / "index"

    def _path(self, vault: str) -> Path:
        return self._dir / f"{vault}.json"

    def _read(self, vault: str) -> dict[str, Any]:
        path = self._path(vault)
        if not path.is_file():
            return {}
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data

    @contextmanager
    def _locked_write(self, vault: str) -> Iterator[dict[str, Any]]:
        """Yield the current index for mutation, then persist it atomically.

        A file lock serializes the read-modify-write against other processes so a
        concurrent writer cannot lose an entry.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path(vault)
        lock_path = path.parent / f"{path.name}.lock"
        with lock_path.open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                index = self._read(vault)
                yield index
                tmp = path.parent / f"{path.name}.{os.getpid()}.tmp"
                tmp.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")
                try:
                    tmp.replace(path)
                except BaseException:
                    tmp.unlink(missing_ok=True)
                    raise
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def get(self, vault: str, sha256: str) -> SourceRecord | None:
        raw = self._read(vault).get(sha256)
        return SourceRecord.model_validate(raw) if raw is not None else None

    def put(self, vault: str, record: SourceRecord) -> None:
        with self._locked_write(vault) as index:
            index[record.sha256] = record.model_dump(mode="json")

    def remove(self, vault: str, sha256: str) -> bool:
        """Delete an entry (retraction, §12.3). Returns whether it existed."""
        with self._locked_write(vault) as index:
            return index.pop(sha256, None) is not None


__all__ = ["SourceIndex"]
