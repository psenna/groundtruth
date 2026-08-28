from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, field_validator

from .note import _SHA256_RE


class SourceRecord(BaseModel):
    """A source-index entry (spec §5, §12.3): what an ingested source produced.

    Lives in the state dir, not the repo, so dedup and provenance survive
    ``raw_archive: off`` (ADR-7).
    """

    model_config = ConfigDict(extra="forbid")

    #: Lowercase hex SHA-256 of the ingested source text.
    sha256: str
    job_id: str
    commit_sha: str
    #: Vault-relative paths of the notes this ingest created or updated.
    notes_touched: list[str]
    ingested_at: date | datetime
    #: Optional human label for the source (filename, URL, …).
    source_label: str | None = None

    @field_validator("sha256")
    @classmethod
    def _is_sha256(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError(f"sha256 {value!r} is not a lowercase hex SHA-256 digest")
        return value
