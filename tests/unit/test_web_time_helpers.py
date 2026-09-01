from __future__ import annotations

from datetime import UTC, datetime, timedelta

from groundtruth.models import JobRecord, JobState
from groundtruth.web.views import _ran, _waited

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
