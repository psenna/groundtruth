"""Exact-hash dedup against the source index (spec §7.2).

A hit skips all LLM work and returns the prior result — the cheapest path
through the pipeline. Exact-match only: whitespace or encoding differences
produce a different hash and a fresh ingest (near-duplicate detection is a
non-goal, §3.3).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..models import JobRecord, SourceRecord
from ..storage.source_index import SourceIndex


def content_hash(text: str) -> str:
    """SHA-256 hex of the decoded text. Stable across platforms — the input is a ``str``."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DedupHit:
    """A previous ingest of byte-identical text."""

    sha256: str
    prior: SourceRecord


def check_dedup(vault: str, text: str, index: SourceIndex) -> DedupHit | None:
    """Return the prior ingest for ``text`` in ``vault``, or ``None`` on a miss."""
    sha = content_hash(text)
    prior = index.get(vault, sha)
    return DedupHit(sha256=sha, prior=prior) if prior is not None else None


def mark_deduped(job: JobRecord, hit: DedupHit) -> JobRecord:
    """Annotate ``job`` as a dedup short-circuit so it is distinguishable from a fresh ingest."""
    return job.model_copy(
        update={
            "dedup_of": hit.prior.job_id,
            "commit_sha": hit.prior.commit_sha,
            "source_sha": hit.sha256,
        }
    )


__all__ = ["DedupHit", "check_dedup", "content_hash", "mark_deduped"]
