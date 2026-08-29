"""Raw archive writer (spec §7.8).

With ``raw_archive: true`` the original text is preserved under ``<repo>/external/``
so a claim can be re-read at its source. It is optional — dedup and provenance
(the source index, #10) do not depend on it (ADR-7). ``external/`` sits inside the
repo but outside the vault directory (§5), so Obsidian never sees it.

The ``.txt`` is immutable once written. The manifest's ``commit_sha`` is the only
field written after the fact, once the commit exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

_ARCHIVE_DIRNAME = "external"


@dataclass(frozen=True)
class ArchiveResult:
    text_path: Path
    manifest_path: Path


def _archive_dir(repo_root: Path | str) -> Path:
    return Path(repo_root) / _ARCHIVE_DIRNAME


def write_archive(
    repo_root: Path | str,
    *,
    sha256: str,
    text: str,
    source_label: str,
    job_id: str,
    ingested_at: date | datetime,
    notes_touched: list[str],
    enabled: bool,
) -> ArchiveResult | None:
    """Write ``external/<sha>.txt`` and ``external/<sha>.json`` when ``enabled``.

    Returns the paths, or ``None`` when archiving is off. Never rewrites an
    existing ``.txt`` (immutability).
    """
    if not enabled:
        return None

    archive_dir = _archive_dir(repo_root)
    archive_dir.mkdir(parents=True, exist_ok=True)
    text_path = archive_dir / f"{sha256}.txt"
    manifest_path = archive_dir / f"{sha256}.json"

    if not text_path.exists():
        text_path.write_text(text, encoding="utf-8")

    if not manifest_path.exists():
        manifest = {
            "hash": sha256,
            "ingested_at": ingested_at.isoformat(),
            "source_label": source_label,
            "job_id": job_id,
            "commit_sha": None,
            "notes_touched": list(notes_touched),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return ArchiveResult(text_path=text_path, manifest_path=manifest_path)


def set_commit_sha(repo_root: Path | str, sha256: str, commit_sha: str) -> None:
    """Record the commit sha in an existing manifest, after the commit exists."""
    manifest_path = _archive_dir(repo_root) / f"{sha256}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["commit_sha"] = commit_sha
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


__all__ = ["ArchiveResult", "set_commit_sha", "write_archive"]
