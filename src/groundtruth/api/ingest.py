"""Ingest and job endpoints (spec §10.1, §4.4, §12.1). Adapter only — delegates
entirely to the queue (#27) via :class:`Services`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..models import JobRecord, JobState
from ..redaction import redact
from .services import Services

#: Upper bound on how long ``wait=true`` blocks before degrading to the job id.
_MAX_WAIT_SECONDS = 120.0


class IngestRequest(BaseModel):
    vault: str
    text: str = Field(min_length=1)
    source_label: str = "api"


class JobResponse(BaseModel):
    id: str
    vault: str
    state: JobState
    deduplicated: bool
    failure_stage: str | None = None
    error: str | None = None
    commit_sha: str | None = None
    notes_created: list[str] = []
    notes_updated: list[str] = []
    attempts: int = 1
    attempt_errors: list[str] = []
    source_sha: str | None = None
    source_bytes: int | None = None
    stage_timings: dict[str, float] = {}
    token_usage: dict[str, int] = {}
    created_at: datetime | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def of(cls, record: JobRecord) -> JobResponse:
        return cls(
            id=record.id,
            vault=record.vault,
            state=record.state,
            deduplicated=record.dedup_of is not None,
            failure_stage=record.failure_stage,
            error=redact(record.error) if record.error else None,
            commit_sha=record.commit_sha,
            notes_created=record.notes_created,
            notes_updated=record.notes_updated,
            attempts=record.attempts,
            attempt_errors=[redact(e) for e in record.attempt_errors],
            source_sha=record.source_sha,
            source_bytes=record.source_bytes,
            stage_timings=record.stage_timings,
            token_usage=record.token_usage,
            created_at=record.created_at,
            started_at=record.started_at,
            updated_at=record.updated_at,
        )


def build_ingest_router(services: Services) -> APIRouter:
    router = APIRouter()

    @router.post("/ingest")
    def ingest(request: IngestRequest, wait: bool = False) -> dict[str, Any]:
        result = services.submit_ingest(
            request.vault,
            request.text,
            request.source_label,
            wait=wait,
            timeout=_MAX_WAIT_SECONDS,
        )
        if isinstance(result, str):
            return {"id": result, "state": JobState.QUEUED.value}
        return JobResponse.of(result).model_dump()

    @router.get("/jobs")
    def list_jobs(limit: int = 100) -> list[dict[str, Any]]:
        return [JobResponse.of(r).model_dump() for r in services.list_recent_jobs(limit)]

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        return JobResponse.of(services.get_job(job_id)).model_dump()

    return router


__all__ = ["build_ingest_router"]
