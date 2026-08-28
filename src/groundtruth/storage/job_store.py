"""Job record persistence with a retention sweep (spec §4.4, §12.1).

One JSON file per job at ``<state-dir>/jobs/<job-id>.json``, surviving restarts.
Records never contain secrets (invariant 6, §11.4).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..errors import GroundtruthError
from ..models import TERMINAL_JOB_STATES, JobRecord

#: Substrings/patterns that indicate a leaked credential in a would-be job record.
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"[Bb]earer\s+[A-Za-z0-9._~+/-]{20,}"),
)
_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class JobStoreError(GroundtruthError):
    """A job store operation failed."""


def _assert_no_secrets(record: JobRecord) -> None:
    blob = json.dumps(record.model_dump(mode="json"))
    for pattern in _SECRET_PATTERNS:
        if pattern.search(blob):
            raise JobStoreError(
                "job record appears to contain a secret; secrets are environment "
                "variables only (§11.4)"
            )


class JobStore:
    """Create, update, load and sweep job records under one state dir."""

    def __init__(self, state_dir: Path | str, *, retention_days: int = 7) -> None:
        self._dir = Path(state_dir) / "jobs"
        self._retention_days = retention_days

    def _path(self, job_id: str) -> Path:
        if not _ID_RE.match(job_id):
            raise JobStoreError(f"unsafe job id {job_id!r}")
        return self._dir / f"{job_id}.json"

    def _write(self, record: JobRecord) -> None:
        _assert_no_secrets(record)
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path(record.id)
        tmp = path.parent / f"{path.name}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(record.model_dump(mode="json"), indent=2), encoding="utf-8")
        try:
            tmp.replace(path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def load(self, job_id: str) -> JobRecord | None:
        path = self._path(job_id)
        if not path.is_file():
            return None
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return JobRecord.model_validate(data)

    def create(self, record: JobRecord) -> JobRecord:
        if self._path(record.id).exists():
            raise JobStoreError(f"job {record.id!r} already exists")
        self._write(record)
        return record

    def update(self, record: JobRecord) -> JobRecord:
        existing = self.load(record.id)
        if existing is None:
            raise JobStoreError(f"no such job {record.id!r}")
        if record.state != existing.state and not existing.can_transition_to(record.state):
            raise ValueError(
                f"illegal job transition: {existing.state.value} -> {record.state.value}"
            )
        self._write(record)
        return record

    def list_ids(self) -> list[str]:
        if not self._dir.is_dir():
            return []
        return sorted(p.stem for p in self._dir.glob("*.json"))

    def sweep(self, *, now: datetime | None = None) -> list[str]:
        """Delete terminal job records older than ``retention_days``. Returns removed ids.

        A job in a non-terminal state is never deleted, regardless of age.
        """
        cutoff = (now or datetime.now()) - timedelta(days=self._retention_days)
        removed: list[str] = []
        for path in sorted(self._dir.glob("*.json")) if self._dir.is_dir() else []:
            record = self.load(path.stem)
            if record is None or record.state not in TERMINAL_JOB_STATES:
                continue
            if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                path.unlink()
                removed.append(record.id)
        return removed


__all__ = ["JobStore", "JobStoreError"]
