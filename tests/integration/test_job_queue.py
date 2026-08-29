from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from groundtruth.jobs.queue import JobQueue
from groundtruth.models import JobRecord, JobState
from groundtruth.storage.job_store import JobStore

pytestmark = pytest.mark.integration


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path)


def _seed(store: JobStore, job_id: str, vault: str = "work") -> None:
    store.create(JobRecord(id=job_id, vault=vault))


def _finish(store: JobStore, job_id: str, state: JobState = JobState.SUCCEEDED) -> JobRecord:
    running = store.update(store.load(job_id).transitioned_to(JobState.RUNNING))
    return store.update(running.transitioned_to(state))


class TestSerialization:
    def test_same_vault_jobs_run_in_order_never_overlapping(self, store: JobStore) -> None:
        events: list[str] = []

        def runner(job_id: str) -> JobRecord:
            events.append(f"{job_id}-start")
            time.sleep(0.05)
            events.append(f"{job_id}-end")
            return _finish(store, job_id)

        queue = JobQueue(store, runner)
        for jid in ("a", "b", "c"):
            _seed(store, jid)
            queue.submit("work", jid)
        for jid in ("a", "b", "c"):
            queue.wait(jid, timeout=5)

        assert events == ["a-start", "a-end", "b-start", "b-end", "c-start", "c-end"]

    def test_different_vaults_run_concurrently(self, store: JobStore) -> None:
        both_started = threading.Barrier(2, timeout=5)
        overlapped = threading.Event()

        def runner(job_id: str) -> JobRecord:
            both_started.wait()  # deadlocks unless the two run at the same time
            overlapped.set()
            return _finish(store, job_id)

        queue = JobQueue(store, runner)
        _seed(store, "j-work", "work")
        _seed(store, "j-personal", "personal")
        queue.submit("work", "j-work")
        queue.submit("personal", "j-personal")
        queue.wait("j-work", timeout=5)
        queue.wait("j-personal", timeout=5)
        assert overlapped.is_set()

    def test_failing_job_does_not_block_the_queue(self, store: JobStore) -> None:
        def runner(job_id: str) -> JobRecord:
            return _finish(
                store, job_id, JobState.FAILED if job_id == "bad" else JobState.SUCCEEDED
            )

        queue = JobQueue(store, runner)
        for jid in ("bad", "good"):
            _seed(store, jid)
            queue.submit("work", jid)

        assert queue.wait("bad", timeout=5).state is JobState.FAILED
        assert queue.wait("good", timeout=5).state is JobState.SUCCEEDED


class TestApi:
    def test_wait_true_blocks_wait_false_returns_id(self, store: JobStore) -> None:
        def runner(job_id: str) -> JobRecord:
            time.sleep(0.02)
            return _finish(store, job_id)

        queue = JobQueue(store, runner)
        _seed(store, "x")
        assert queue.submit("work", "x") == "x"  # immediate

        _seed(store, "y")
        result = queue.submit_and_wait("work", "y", timeout=5)
        assert result.state is JobState.SUCCEEDED

    def test_queue_depth_and_position_observable(self, store: JobStore) -> None:
        gate = threading.Event()

        def runner(job_id: str) -> JobRecord:
            gate.wait(5)
            return _finish(store, job_id)

        queue = JobQueue(store, runner)
        for jid in ("a", "b", "c"):
            _seed(store, jid)
            queue.submit("work", jid)

        time.sleep(0.05)  # let the worker pick up "a"
        assert queue.depth("work") == 3
        assert queue.position_of("a").position == 0
        assert queue.position_of("b").position == 1
        assert queue.position_of("c").position == 2
        gate.set()
        for jid in ("a", "b", "c"):
            queue.wait(jid, timeout=5)
        assert queue.position_of("a").position is None


class TestShutdown:
    def test_shutdown_without_drain_leaves_queued_jobs_untouched(self, store: JobStore) -> None:
        started = threading.Event()
        release = threading.Event()

        def runner(job_id: str) -> JobRecord:
            started.set()
            release.wait(5)
            return _finish(store, job_id)

        queue = JobQueue(store, runner)
        for jid in ("running", "queued"):
            _seed(store, jid)
            queue.submit("work", jid)
        started.wait(5)

        release.set()
        queue.shutdown(drain=False, timeout=5)

        assert store.load("running").state is JobState.SUCCEEDED
        assert store.load("queued").state is JobState.QUEUED  # never started, record intact

    def test_shutdown_with_drain_finishes_everything(self, store: JobStore) -> None:
        def runner(job_id: str) -> JobRecord:
            return _finish(store, job_id)

        queue = JobQueue(store, runner)
        for jid in ("a", "b"):
            _seed(store, jid)
            queue.submit("work", jid)
        queue.shutdown(drain=True, timeout=5)
        assert store.load("a").state is JobState.SUCCEEDED
        assert store.load("b").state is JobState.SUCCEEDED
