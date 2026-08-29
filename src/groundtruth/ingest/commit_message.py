"""Commit-message formatter (spec §7.9).

Pure function. The excerpt is truncated and passed through redaction so a secret
in ingested content never lands in git history (invariant 6, §11.4).
"""

from __future__ import annotations

from collections.abc import Sequence

from ..redaction import redact

_EXCERPT_MAX_CHARS = 200


def _join(names: Sequence[str]) -> str:
    return ", ".join(names)


def format_commit_message(
    *,
    vault: str,
    subject: str,
    created: Sequence[str],
    updated: Sequence[str],
    tags: Sequence[str],
    source_sha: str,
    job_id: str,
    excerpt: str,
) -> str:
    """Render the §7.9 commit message: action, vault, notes, tags, source hash, excerpt."""
    notes_parts: list[str] = []
    if created:
        notes_parts.append(f"created {_join(created)}")
    if updated:
        notes_parts.append(f"updated {_join(updated)}")
    notes_line = " · ".join(notes_parts) if notes_parts else "none"

    clipped = " ".join(excerpt.split())[:_EXCERPT_MAX_CHARS]
    safe_excerpt = redact(clipped).strip()

    return (
        f"ingest({vault}): {subject}\n"
        f"\n"
        f"notes:   {notes_line}\n"
        f"tags:    {_join(tags)}\n"
        f"source:  sha256:{source_sha}\n"
        f"job:     {job_id}\n"
        f"\n"
        f"{safe_excerpt}\n"
    )


__all__ = ["format_commit_message"]
