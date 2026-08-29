from __future__ import annotations

from pathlib import Path

import pytest

from groundtruth.errors import (
    DirtyWorkingTreeError,
    GitConflictError,
    MalformedLLMOutputError,
    ModelServerConnectionError,
    ReadTimeoutError,
    TransientHTTPError,
    WriteValidationError,
)
from groundtruth.jobs.retry import retrying_runner, run_with_retry
from groundtruth.models import JobRecord, JobState
from groundtruth.storage.job_store import JobStore

TRANSIENT = [
    ModelServerConnectionError("refused"),
    ReadTimeoutError("timeout"),
    TransientHTTPError(503),
]
TERMINAL = [
    WriteValidationError("bad path"),
    GitConflictError("non-ff"),
    DirtyWorkingTreeError("dirty"),
    MalformedLLMOutputError("not json"),
]


class TestRunWithRetry:
    def test_transient_retries_twice_then_fails(self) -> None:
        slept: list[float] = []
        calls = {"n": 0}

        def attempt(_n: int) -> str:
            calls["n"] += 1
            raise TransientHTTPError(503)

        result = run_with_retry(attempt, sleep=slept.append, backoff_base=1.0)
        assert result.succeeded is False
        assert result.attempts == 3  # initial + 2 retries
        assert calls["n"] == 3
        assert len(result.errors) == 3

    def test_backoff_increases_and_is_injectable(self) -> None:
        slept: list[float] = []

        def attempt(_n: int) -> str:
            raise ReadTimeoutError("t")

        run_with_retry(attempt, sleep=slept.append, backoff_base=2.0)
        assert slept == [2.0, 4.0]  # increasing, and nothing actually slept

    @pytest.mark.parametrize("exc", TERMINAL)
    def test_terminal_never_retries(self, exc: Exception) -> None:
        calls = {"n": 0}

        def attempt(_n: int) -> str:
            calls["n"] += 1
            raise exc

        slept: list[float] = []
        result = run_with_retry(attempt, sleep=slept.append)
        assert calls["n"] == 1  # exactly one attempt
        assert slept == []  # surfaced immediately, undelayed
        assert result.final_error is exc

    def test_success_on_retry_two_is_recorded_with_attempt_count(self) -> None:
        def attempt(n: int) -> str:
            if n < 3:
                raise ModelServerConnectionError("restarting")
            return "ok"

        result = run_with_retry(attempt, sleep=lambda _s: None)
        assert result.succeeded is True
        assert result.value == "ok"
        assert result.attempts == 3
        assert len(result.errors) == 2

    def test_first_attempt_success(self) -> None:
        result = run_with_retry(lambda _n: "done", sleep=lambda _s: None)
        assert result.attempts == 1 and result.succeeded


class TestRetryingRunner:
    @pytest.fixture
    def store(self, tmp_path: Path) -> JobStore:
        return JobStore(tmp_path)

    def _seed(self, store: JobStore, job_id: str = "j") -> None:
        store.create(JobRecord(id=job_id, vault="work"))
        store.update(store.load(job_id).transitioned_to(JobState.RUNNING))

    def test_transient_then_success_is_persisted_with_attempts(self, store: JobStore) -> None:
        self._seed(store)
        calls = {"n": 0}

        def inner(job_id: str) -> JobRecord:
            calls["n"] += 1
            if calls["n"] < 3:
                raise TransientHTTPError(502)
            return store.update(store.load(job_id).transitioned_to(JobState.SUCCEEDED))

        run = retrying_runner(inner, store, sleep=lambda _s: None)
        result = run("j")

        assert result.state is JobState.SUCCEEDED
        assert result.attempts == 3
        assert len(result.attempt_errors) == 2
        assert store.load("j").attempts == 3

    def test_terminal_failure_is_one_attempt(self, store: JobStore) -> None:
        self._seed(store)
        calls = {"n": 0}

        def inner(job_id: str) -> JobRecord:
            calls["n"] += 1
            running = store.load(job_id)
            return store.update(
                running.model_copy(update={"failure_stage": "write-validation"}).transitioned_to(
                    JobState.FAILED
                )
            )

        run = retrying_runner(inner, store, sleep=lambda _s: None)
        result = run("j")

        assert result.state is JobState.FAILED
        assert calls["n"] == 1
        assert result.attempts == 1

    def test_exhausted_transient_marks_failed(self, store: JobStore) -> None:
        self._seed(store)

        def inner(job_id: str) -> JobRecord:
            raise ModelServerConnectionError("still down")

        run = retrying_runner(inner, store, sleep=lambda _s: None)
        result = run("j")

        assert result.state is JobState.FAILED
        assert result.attempts == 3
        assert result.failure_stage == "retry-exhausted"
        assert len(result.attempt_errors) == 3


def test_classification_has_no_second_taxonomy() -> None:
    source = (Path(__file__).parents[2] / "src/groundtruth/jobs/retry.py").read_text()
    assert "is_transient" in source
    # no re-listing of transient/terminal error types
    assert "429" not in source and "502" not in source
