from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from groundtruth.models import JobRecord, JobState
from groundtruth.storage.job_store import JobStore, JobStoreError

pytestmark = pytest.mark.integration


def _job(job_id: str = "01J8X", state: JobState = JobState.QUEUED) -> JobRecord:
    return JobRecord(id=job_id, vault="work", state=state)


def _age_file(path: Path, days: float) -> None:
    when = (datetime.now() - timedelta(days=days)).timestamp()
    os.utime(path, (when, when))


class TestPersistence:
    def test_create_update_load(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path)
        store.create(_job())
        running = store.update(_job(state=JobState.RUNNING))
        assert running.state is JobState.RUNNING
        assert store.load("01J8X").state is JobState.RUNNING

    def test_load_unknown_is_none(self, tmp_path: Path) -> None:
        assert JobStore(tmp_path).load("nope") is None

    def test_records_survive_restart(self, tmp_path: Path) -> None:
        JobStore(tmp_path).create(_job(state=JobState.RUNNING))
        reopened = JobStore(tmp_path)
        assert reopened.load("01J8X").state is JobState.RUNNING

    def test_stored_under_state_dir_jobs(self, tmp_path: Path) -> None:
        JobStore(tmp_path).create(_job())
        assert (tmp_path / "jobs" / "01J8X.json").is_file()


class TestTimestampsAndListing:
    def test_create_stamps_created_and_updated(self, tmp_path: Path) -> None:
        t = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        store = JobStore(tmp_path, now=lambda: t)
        store.create(_job())
        rec = store.load("01J8X")
        assert rec is not None and rec.created_at == t and rec.updated_at == t

    def test_update_advances_only_updated_at(self, tmp_path: Path) -> None:
        clock = iter([datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)])
        store = JobStore(tmp_path, now=lambda: next(clock))
        store.create(_job())
        store.update(_job(state=JobState.RUNNING))
        rec = store.load("01J8X")
        assert rec is not None
        assert rec.created_at == datetime(2026, 1, 1, tzinfo=UTC)
        assert rec.updated_at == datetime(2026, 1, 2, tzinfo=UTC)

    def test_started_at_stamped_once_on_running_transition(self, tmp_path: Path) -> None:
        times = iter(datetime(2026, 1, d, tzinfo=UTC) for d in (1, 5, 9))
        store = JobStore(tmp_path, now=lambda: next(times))
        store.create(_job())  # day 1
        assert store.load("01J8X").started_at is None
        store.update(_job(state=JobState.RUNNING))  # day 5 -> started
        store.update(_job(state=JobState.SUCCEEDED))  # day 9 -> started unchanged
        rec = store.load("01J8X")
        assert rec is not None
        assert rec.started_at == datetime(2026, 1, 5, tzinfo=UTC)  # the RUNNING time, not day 9

    def test_started_at_stays_none_for_a_job_that_never_ran(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path)
        store.create(_job())
        store.update(_job(state=JobState.FAILED))  # QUEUED -> FAILED (restart recovery)
        assert store.load("01J8X").started_at is None

    def test_list_recent_is_newest_activity_first_and_limited(self, tmp_path: Path) -> None:
        times = iter(datetime(2026, 1, d, tzinfo=UTC) for d in (1, 2, 3, 4, 5, 6))
        store = JobStore(tmp_path, now=lambda: next(times))
        for i in (1, 2, 3):
            store.create(_job(f"job{i}"))
        store.update(JobRecord(id="job1", vault="work", state=JobState.RUNNING))  # newest touch
        recent = store.list_recent(limit=2)
        assert [r.id for r in recent] == ["job1", "job3"]

    def test_list_recent_empty_when_no_jobs(self, tmp_path: Path) -> None:
        assert JobStore(tmp_path).list_recent() == []


class TestTokenUsageRoundTrip:
    def test_token_counts_survive_a_write_and_reload(self, tmp_path: Path) -> None:
        from groundtruth.models import TokenCounts

        store = JobStore(tmp_path)
        store.create(_job())
        job = _job(state=JobState.RUNNING).model_copy(
            update={
                "token_usage": {
                    "survey": TokenCounts(prompt_tokens=3, completion_tokens=2, total_tokens=5),
                    "organize": TokenCounts(prompt_tokens=10, completion_tokens=4, total_tokens=14),
                }
            }
        )
        store.update(job)

        reloaded = JobStore(tmp_path).load("01J8X")
        assert reloaded is not None
        assert reloaded.token_usage["survey"] == TokenCounts(
            prompt_tokens=3, completion_tokens=2, total_tokens=5
        )
        assert reloaded.token_usage["organize"].total_tokens == 14

    def test_a_legacy_int_record_on_disk_still_loads(self, tmp_path: Path) -> None:
        import json

        from groundtruth.models import TokenCounts

        store = JobStore(tmp_path)
        store.create(_job())
        path = tmp_path / "jobs" / "01J8X.json"
        raw = json.loads(path.read_text())
        raw["token_usage"] = {"reduce": 99}
        path.write_text(json.dumps(raw))

        reloaded = JobStore(tmp_path).load("01J8X")
        assert reloaded is not None
        assert reloaded.token_usage["reduce"] == TokenCounts(total_tokens=99)


class TestTransitions:
    def test_legal_transition_accepted(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path)
        store.create(_job())
        store.update(_job(state=JobState.RUNNING))
        store.update(_job(state=JobState.SUCCEEDED))

    def test_same_state_update_is_allowed(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path)
        store.create(_job())
        job = _job()
        job = job.model_copy(update={"stage_timings": {"survey": 1.2}})
        assert store.update(job).stage_timings == {"survey": 1.2}

    def test_illegal_transition_rejected(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path)
        store.create(_job())
        with pytest.raises(ValueError):
            store.update(_job(state=JobState.SUCCEEDED))  # QUEUED -> SUCCEEDED

    def test_update_unknown_job_raises(self, tmp_path: Path) -> None:
        with pytest.raises(JobStoreError):
            JobStore(tmp_path).update(_job())


class TestSweep:
    def test_deletes_old_terminal_keeps_new(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path, retention_days=7)
        store.create(_job("old"))
        store.update(JobRecord(id="old", vault="work", state=JobState.RUNNING))
        store.update(JobRecord(id="old", vault="work", state=JobState.SUCCEEDED))
        store.create(_job("new"))
        store.update(JobRecord(id="new", vault="work", state=JobState.RUNNING))
        store.update(JobRecord(id="new", vault="work", state=JobState.FAILED))

        _age_file(tmp_path / "jobs" / "old.json", days=10)
        _age_file(tmp_path / "jobs" / "new.json", days=1)

        removed = store.sweep()
        assert removed == ["old"]
        assert store.load("old") is None
        assert store.load("new") is not None

    def test_never_deletes_non_terminal_regardless_of_age(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path, retention_days=7)
        store.create(_job("ancient"))
        store.update(JobRecord(id="ancient", vault="work", state=JobState.RUNNING))
        _age_file(tmp_path / "jobs" / "ancient.json", days=9999)
        assert store.sweep() == []
        assert store.load("ancient") is not None


class TestSecrets:
    def test_env_secret_value_is_not_serialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GT_API_KEY", "sk-super-secret-value")
        store = JobStore(tmp_path)
        store.create(_job())
        contents = (tmp_path / "jobs" / "01J8X.json").read_text()
        assert "sk-super-secret-value" not in contents

    def test_secret_shaped_field_is_rejected(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path)
        store.create(_job())
        leaky = JobRecord(
            id="01J8X",
            vault="work",
            state=JobState.RUNNING,
            error="model call failed: Authorization: Bearer sk-ABCDEF0123456789ABCDEF",
        )
        with pytest.raises(JobStoreError):
            store.update(leaky)


class TestAtomicWrite:
    def test_crash_before_rename_leaves_prior_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = JobStore(tmp_path)
        store.create(_job())

        def boom(self: Path, target: Path) -> None:
            raise OSError("crash")

        monkeypatch.setattr(Path, "replace", boom)
        with pytest.raises(OSError, match="crash"):
            store.update(_job(state=JobState.RUNNING))
        monkeypatch.undo()

        assert store.load("01J8X").state is JobState.QUEUED
        assert not list((tmp_path / "jobs").glob("*.tmp"))
