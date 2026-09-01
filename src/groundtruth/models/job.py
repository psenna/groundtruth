from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .usage import TokenCounts


class JobState(StrEnum):
    """Lifecycle states of an ingest job (spec §4.4, §12.1)."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


#: The only permitted state transitions. SUCCEEDED and FAILED are terminal.
LEGAL_JOB_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset({JobState.RUNNING, JobState.FAILED}),
    JobState.RUNNING: frozenset({JobState.SUCCEEDED, JobState.FAILED}),
    JobState.SUCCEEDED: frozenset(),
    JobState.FAILED: frozenset(),
}

TERMINAL_JOB_STATES: frozenset[JobState] = frozenset({JobState.SUCCEEDED, JobState.FAILED})


class JobRecord(BaseModel):
    """One ingest job, persisted as JSON in ``<state-dir>/jobs/<id>.json`` (spec §12.1)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    vault: str
    state: JobState = JobState.QUEUED

    #: Set by the JobStore on create / on every state write. Optional so records
    #: written before this field existed still load. ``started_at`` is stamped
    #: once, on the QUEUED -> RUNNING transition, so wait time
    #: (``started_at - created_at``) and run time can be told apart.
    created_at: datetime | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None

    #: Per-stage wall-clock seconds (spec §12.4).
    stage_timings: dict[str, float] = Field(default_factory=dict)
    #: Token usage keyed by pipeline stage (``survey``, ``reduce``, ``organize``,
    #: ``tag``). Records written before #116 stored a bare ``int`` per key; the
    #: validator below widens those so old job files still load.
    token_usage: dict[str, TokenCounts] = Field(default_factory=dict)

    @field_validator("token_usage", mode="before")
    @classmethod
    def _widen_legacy_token_usage(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        widened: dict[Any, Any] = {}
        for key, entry in value.items():
            if isinstance(entry, bool):
                widened[key] = entry
            elif isinstance(entry, int):
                widened[key] = {"total_tokens": entry}
            else:
                widened[key] = entry
        return widened

    notes_created: list[str] = Field(default_factory=list)
    notes_updated: list[str] = Field(default_factory=list)

    #: Terminal dangling-link downgrades (§7.6): note path -> the link targets
    #: whose ``[[ ]]`` markup was stripped on the final organize attempt. Empty
    #: unless that last-resort path fired.
    link_downgrades: dict[str, list[str]] = Field(default_factory=dict)

    source_sha: str | None = None
    #: Size in UTF-8 bytes of the ingested text.
    source_bytes: int | None = None
    commit_sha: str | None = None

    #: On failure: the stage that failed and the error message.
    failure_stage: str | None = None
    error: str | None = None

    #: On a dedup short-circuit: the id of the prior ingest job (spec §7.11).
    dedup_of: str | None = None

    #: Retry accounting (spec §12.2). ``attempts`` counts every run of the job;
    #: ``attempt_errors`` holds the error from each failed attempt, in order.
    attempts: int = 1
    attempt_errors: list[str] = Field(default_factory=list)

    def can_transition_to(self, new_state: JobState) -> bool:
        return new_state in LEGAL_JOB_TRANSITIONS[self.state]

    def transitioned_to(self, new_state: JobState) -> JobRecord:
        """Return a copy in ``new_state``; raise ``ValueError`` if the move is illegal."""
        if not self.can_transition_to(new_state):
            raise ValueError(f"illegal job transition: {self.state.value} -> {new_state.value}")
        return self.model_copy(update={"state": new_state})
