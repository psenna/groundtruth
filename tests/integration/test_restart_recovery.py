from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from groundtruth.jobs.recovery import recover_on_startup
from groundtruth.models import JobRecord, JobState
from groundtruth.storage.git import GitRepo
from groundtruth.storage.job_store import JobStore

pytestmark = pytest.mark.integration


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "work").mkdir(parents=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "work" / "seed.md").write_text("seed\n")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=s", "-c", "user.email=s@s", "commit", "-m", "seed")
    return root


def _digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted((root / "jobs").rglob("*")):
        if p.is_file():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()


class TestRecovery:
    def test_queued_jobs_requeued_in_order(self, tmp_path: Path, repo: Path) -> None:
        store = JobStore(tmp_path / "state")
        for jid in ("01A", "01B", "01C"):
            store.create(JobRecord(id=jid, vault="work"))

        submitted: list[str] = []
        report = recover_on_startup(
            store,
            repo_root_of=lambda _v: repo,
            resubmit=lambda _v, jid: submitted.append(jid),
        )
        assert report.requeued == ["01A", "01B", "01C"]
        assert submitted == ["01A", "01B", "01C"]

    def test_interrupted_job_is_failed_and_vault_rolled_back(
        self, tmp_path: Path, repo: Path
    ) -> None:
        store = JobStore(tmp_path / "state")
        store.create(JobRecord(id="01RUN", vault="work"))
        store.update(store.load("01RUN").transitioned_to(JobState.RUNNING))
        # a crash left half-written state in the vault
        (repo / "work" / "half.md").write_text("half-written note\n")
        (repo / "work" / "seed.md").write_text("tampered\n")

        report = recover_on_startup(store, repo_root_of=lambda _v: repo)

        job = store.load("01RUN")
        assert job.state is JobState.FAILED
        assert job.failure_stage == "restart"
        assert "not resumed" in job.error
        assert report.failed_interrupted == ["01RUN"]
        assert GitRepo(repo).is_clean()
        assert not (repo / "work" / "half.md").exists()
        assert (repo / "work" / "seed.md").read_text() == "seed\n"

    def test_terminal_jobs_are_untouched(self, tmp_path: Path, repo: Path) -> None:
        store = JobStore(tmp_path / "state")
        store.create(JobRecord(id="01OK", vault="work"))
        running = store.update(store.load("01OK").transitioned_to(JobState.RUNNING))
        store.update(running.transitioned_to(JobState.SUCCEEDED))
        before = store.load("01OK")

        recover_on_startup(store, repo_root_of=lambda _v: repo)
        assert store.load("01OK") == before

    def test_retention_sweep_runs_on_startup(self, tmp_path: Path, repo: Path) -> None:
        import os
        from datetime import datetime, timedelta

        store = JobStore(tmp_path / "state", retention_days=7)
        store.create(JobRecord(id="01OLD", vault="work"))
        r = store.update(store.load("01OLD").transitioned_to(JobState.RUNNING))
        store.update(r.transitioned_to(JobState.SUCCEEDED))
        old = (datetime.now() - timedelta(days=30)).timestamp()
        os.utime(tmp_path / "state" / "jobs" / "01OLD.json", (old, old))

        report = recover_on_startup(store, repo_root_of=lambda _v: repo)
        assert report.swept == ["01OLD"]
        assert store.load("01OLD") is None

    def test_corrupt_record_is_quarantined_not_crashed(self, tmp_path: Path, repo: Path) -> None:
        store = JobStore(tmp_path / "state")
        store.create(JobRecord(id="01GOOD", vault="work"))
        (tmp_path / "state" / "jobs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "state" / "jobs" / "01BAD.json").write_text("{not valid json")

        report = recover_on_startup(store, repo_root_of=lambda _v: repo)

        assert report.quarantined == ["01BAD"]
        assert not (tmp_path / "state" / "jobs" / "01BAD.json").exists()
        assert (tmp_path / "state" / "jobs" / "quarantine" / "01BAD.json").exists()
        assert store.load("01GOOD") is not None  # good record still processed

    def test_recovery_is_idempotent(self, tmp_path: Path, repo: Path) -> None:
        store = JobStore(tmp_path / "state")
        store.create(JobRecord(id="01RUN", vault="work"))
        store.update(store.load("01RUN").transitioned_to(JobState.RUNNING))
        store.create(JobRecord(id="01Q", vault="work"))

        recover_on_startup(store, repo_root_of=lambda _v: repo)
        digest_after_first = _digest(tmp_path / "state")
        recover_on_startup(store, repo_root_of=lambda _v: repo)
        assert _digest(tmp_path / "state") == digest_after_first
