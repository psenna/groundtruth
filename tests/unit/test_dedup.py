from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest

from groundtruth.ingest.dedup import check_dedup, content_hash, mark_deduped
from groundtruth.models import JobRecord, JobState, SourceRecord
from groundtruth.storage.source_index import SourceIndex

pytestmark = pytest.mark.integration

TEXT = "Acme Corp ships widgets and was founded in 1996."


class TestContentHash:
    def test_identical_text_same_hash(self) -> None:
        assert content_hash(TEXT) == content_hash(TEXT)

    def test_hash_is_sha256_of_utf8(self) -> None:
        assert content_hash(TEXT) == hashlib.sha256(TEXT.encode("utf-8")).hexdigest()

    def test_hash_is_over_decoded_text(self) -> None:
        # A str in, regardless of how the platform encoded the file it came from.
        assert content_hash("café") == hashlib.sha256("café".encode()).hexdigest()

    def test_trailing_whitespace_changes_the_hash(self) -> None:
        assert content_hash(TEXT) != content_hash(TEXT + " ")
        assert content_hash(TEXT) != content_hash(TEXT + "\n")


class TestCheckDedup:
    def _seed(self, index: SourceIndex, vault: str, text: str, *, job: str = "01PRIOR") -> None:
        index.put(
            vault,
            SourceRecord(
                sha256=content_hash(text),
                job_id=job,
                commit_sha="c0ffee",
                notes_touched=["companies/Acme.md"],
                ingested_at=date(2026, 8, 1),
            ),
        )

    def test_hit_returns_prior_without_llm(self, tmp_path: Path) -> None:
        index = SourceIndex(tmp_path)
        self._seed(index, "work", TEXT)

        # check_dedup takes no client — a hit is impossible to reach an LLM through.
        hit = check_dedup("work", TEXT, index)
        assert hit is not None
        assert hit.prior.job_id == "01PRIOR"
        assert hit.prior.commit_sha == "c0ffee"
        assert hit.prior.notes_touched == ["companies/Acme.md"]

    def test_miss_returns_none(self, tmp_path: Path) -> None:
        index = SourceIndex(tmp_path)
        self._seed(index, "work", TEXT)
        assert check_dedup("work", "totally different text", index) is None

    def test_dedup_is_per_vault(self, tmp_path: Path) -> None:
        index = SourceIndex(tmp_path)
        self._seed(index, "work", TEXT)
        assert check_dedup("work", TEXT, index) is not None
        assert check_dedup("personal", TEXT, index) is None

    def test_trailing_whitespace_is_a_miss(self, tmp_path: Path) -> None:
        index = SourceIndex(tmp_path)
        self._seed(index, "work", TEXT)
        assert check_dedup("work", TEXT + "\n", index) is None


class TestMarkDeduped:
    def test_short_circuit_is_distinguishable_in_the_job_record(self, tmp_path: Path) -> None:
        index = SourceIndex(tmp_path)
        index.put(
            "work",
            SourceRecord(
                sha256=content_hash(TEXT),
                job_id="01PRIOR",
                commit_sha="c0ffee",
                notes_touched=["companies/Acme.md"],
                ingested_at=date(2026, 8, 1),
            ),
        )
        hit = check_dedup("work", TEXT, index)
        assert hit is not None

        job = JobRecord(id="01NEW", vault="work", state=JobState.RUNNING)
        deduped = mark_deduped(job, hit)

        assert deduped.dedup_of == "01PRIOR"
        assert deduped.commit_sha == "c0ffee"
        assert deduped.source_sha == content_hash(TEXT)
        # a fresh ingest never has dedup_of set
        assert JobRecord(id="x", vault="work").dedup_of is None
