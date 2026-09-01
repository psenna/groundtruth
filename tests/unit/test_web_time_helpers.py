from __future__ import annotations

from datetime import UTC, datetime, timedelta

from groundtruth.models import JobRecord, JobState
from groundtruth.web.views import _bytes, _job_detail, _ran, _waited

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _rec(**kw: object) -> JobRecord:
    return JobRecord(id="j", vault="v", **kw)  # type: ignore[arg-type]


class TestWaited:
    def test_queued_shows_time_since_created(self) -> None:
        rec = _rec(state=JobState.QUEUED, created_at=_T0)
        assert _waited(rec, _T0 + timedelta(seconds=12)) == "12.0s"

    def test_running_shows_only_the_time_it_sat_in_the_queue(self) -> None:
        rec = _rec(
            state=JobState.RUNNING,
            created_at=_T0,
            started_at=_T0 + timedelta(seconds=30),
        )
        # wait is fixed at 30s even though the job has been running a while
        assert _waited(rec, _T0 + timedelta(minutes=10)) == "30.0s"

    def test_no_created_at_is_a_dash(self) -> None:
        assert _waited(_rec(state=JobState.QUEUED), _T0) == "—"


class TestRan:
    def test_running_counts_from_started_to_now(self) -> None:
        rec = _rec(state=JobState.RUNNING, created_at=_T0, started_at=_T0 + timedelta(seconds=5))
        assert _ran(rec, _T0 + timedelta(seconds=125)) == "2.0m"

    def test_terminal_counts_started_to_updated_not_to_now(self) -> None:
        rec = _rec(
            state=JobState.SUCCEEDED,
            created_at=_T0,
            started_at=_T0 + timedelta(seconds=5),
            updated_at=_T0 + timedelta(seconds=20),
        )
        assert _ran(rec, _T0 + timedelta(hours=1)) == "15.0s"

    def test_never_started_is_a_dash(self) -> None:
        rec = _rec(state=JobState.FAILED, created_at=_T0, updated_at=_T0 + timedelta(seconds=1))
        assert _ran(rec, _T0 + timedelta(seconds=2)) == "—"


class TestBytes:
    def test_scales_units(self) -> None:
        assert _bytes(None) == "—"
        assert _bytes(512) == "512 B"
        assert _bytes(2048) == "2.0 KB"
        assert _bytes(3 * 1024 * 1024) == "3.0 MB"


class TestJobDetail:
    def test_shapes_the_full_record_for_the_overlay(self) -> None:
        rec = _rec(
            state=JobState.SUCCEEDED,
            created_at=_T0,
            started_at=_T0 + timedelta(seconds=3),
            updated_at=_T0 + timedelta(seconds=63),
            source_bytes=4096,
            source_sha="abc123",
            commit_sha="def456",
            stage_timings={"retrieval": 12.3, "llm": 40.0},
            token_usage={"reduce": 100, "tag": 20},
            notes_created=["a/b.md"],
            attempt_errors=["first try boom"],
        )
        d = _job_detail(rec, _T0 + timedelta(seconds=63))
        assert d["text_size"] == "4.0 KB"
        assert d["waited"] == "3.0s"
        assert d["ran"] == "1.0m"
        assert d["tokens_total"] == 120
        assert {t["stage"] for t in d["stage_timings"]} == {"retrieval", "llm"}
        assert d["notes_created"] == ["a/b.md"]
        assert d["attempt_errors"] == ["first try boom"]
        assert d["created_at"] == "2026-01-01T12:00:00+00:00"

    def test_token_usage_rows_have_prompt_completion_total(self) -> None:
        from groundtruth.models import TokenCounts

        rec = _rec(
            state=JobState.SUCCEEDED,
            created_at=_T0,
            token_usage={
                "reduce": TokenCounts(prompt_tokens=60, completion_tokens=40, total_tokens=100),
                "tag": TokenCounts(prompt_tokens=12, completion_tokens=8, total_tokens=20),
            },
        )
        d = _job_detail(rec, _T0)
        by_role = {r["role"]: r for r in d["token_usage"]}
        assert by_role["reduce"] == {
            "role": "reduce",
            "prompt_tokens": 60,
            "completion_tokens": 40,
            "total_tokens": 100,
        }
        assert d["tokens_total"] == 120

    def test_redacts_errors(self) -> None:
        rec = _rec(
            state=JobState.FAILED,
            created_at=_T0,
            error="boom sk-ABCDEF0123456789ABCDEFGH",
            attempt_errors=["earlier sk-ABCDEF0123456789ABCDEFGH"],
        )
        d = _job_detail(rec, _T0)
        assert "sk-ABCDEF0123456789ABCDEFGH" not in d["error"]
        assert "sk-ABCDEF0123456789ABCDEFGH" not in d["attempt_errors"][0]
