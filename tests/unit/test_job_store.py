from __future__ import annotations

import os
from datetime import datetime, timedelta
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
