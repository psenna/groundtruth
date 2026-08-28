from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


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

    #: Per-stage wall-clock seconds (spec §12.4).
    stage_timings: dict[str, float] = Field(default_factory=dict)
    #: Token usage keyed by LLM role (``tag``, ``reduce``, ``answer``).
    token_usage: dict[str, int] = Field(default_factory=dict)

    notes_created: list[str] = Field(default_factory=list)
    notes_updated: list[str] = Field(default_factory=list)

    source_sha: str | None = None
    commit_sha: str | None = None

    #: On failure: the stage that failed and the error message.
    failure_stage: str | None = None
    error: str | None = None

    #: On a dedup short-circuit: the id of the prior ingest job (spec §7.11).
    dedup_of: str | None = None

    def can_transition_to(self, new_state: JobState) -> bool:
        return new_state in LEGAL_JOB_TRANSITIONS[self.state]

    def transitioned_to(self, new_state: JobState) -> JobRecord:
        """Return a copy in ``new_state``; raise ``ValueError`` if the move is illegal."""
        if not self.can_transition_to(new_state):
            raise ValueError(f"illegal job transition: {self.state.value} -> {new_state.value}")
        return self.model_copy(update={"state": new_state})
