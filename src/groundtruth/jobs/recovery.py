"""Restart recovery for in-flight jobs (spec §4.4, §12.1, invariant 7).

A job interrupted mid-ingest left its repo in whatever state the crash produced.
Recovery reconciles that honestly: it **never resumes a half-finished ingest**
(that would break all-or-nothing) — it marks the job failed, rolls the vault back
to clean, and only then lets the queue accept new work.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from ..models import TERMINAL_JOB_STATES, JobState
from ..storage.git import GitError, GitRepo
from ..storage.job_store import JobStore

_INTERRUPTED_REASON = (
    "job was RUNNING when the process restarted; not resumed — the ingest is "
    "all-or-nothing, so a half-finished run is failed and rolled back (invariant 7)"
)


@dataclass
class ReconcileReport:
    swept: list[str] = field(default_factory=list)
    requeued: list[str] = field(default_factory=list)
    failed_interrupted: list[str] = field(default_factory=list)
    quarantined: list[str] = field(default_factory=list)
    rolled_back: list[str] = field(default_factory=list)


def recover_on_startup(
    job_store: JobStore,
    *,
    repo_root_of: Callable[[str], Path | str],
    resubmit: Callable[[str, str], None] | None = None,
) -> ReconcileReport:
    """Reconcile the state dir with reality before the queue starts.

    ``repo_root_of(vault_name)`` locates a vault's repo for rollback.
    ``resubmit(vault, job_id)`` re-enqueues a queued job (order preserved); if
    omitted, queued jobs are only reported.
    """
    report = ReconcileReport(swept=list(job_store.sweep()))

    for job_id in job_store.list_ids():  # sorted -> submission order for ULID ids
        try:
            job = job_store.load(job_id)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            job_store.quarantine(job_id, reason=str(exc))
            report.quarantined.append(job_id)
            continue
        if job is None or job.state in TERMINAL_JOB_STATES:
            continue

        if job.state is JobState.RUNNING:
            if _rollback(repo_root_of(job.vault)):
                report.rolled_back.append(job_id)
            failed = job.model_copy(
                update={"failure_stage": "restart", "error": _INTERRUPTED_REASON}
            )
            job_store.update(failed.transitioned_to(JobState.FAILED))
            report.failed_interrupted.append(job_id)
        elif job.state is JobState.QUEUED:
            report.requeued.append(job_id)
            if resubmit is not None:
                resubmit(job.vault, job_id)

    return report


def _rollback(repo_root: Path | str) -> bool:
    try:
        repo = GitRepo(repo_root)
        if repo.is_clean():
            return False
        repo.rollback()
    except GitError:
        return False
    return True


__all__ = ["ReconcileReport", "recover_on_startup"]
