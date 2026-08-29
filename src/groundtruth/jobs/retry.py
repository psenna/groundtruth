"""Retry policy: transient twice, terminal never (spec §12.2).

A local-first design leans on a model server that restarts and OOMs, so retrying
a transient failure earns its keep. Retrying a validator rejection does not — it
fails identically and burns tokens. Classification is delegated entirely to
:func:`groundtruth.errors.is_transient` (#5); there is no second copy of the
taxonomy here.

The policy is applied once, by the worker, wrapping the runner — never scattered
through the pipeline.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..errors import is_transient
from ..models import JobRecord, JobState
from ..storage.job_store import JobStore

#: Transient failures retry this many times *after* the first attempt.
DEFAULT_MAX_RETRIES = 2


@dataclass
class RetryResult[T]:
    value: T | None
    succeeded: bool
    attempts: int
    #: The error string from each failed attempt, in order.
    errors: list[str] = field(default_factory=list)
    #: The exception that ended the sequence (terminal, or the last transient).
    final_error: BaseException | None = None


def run_with_retry[T](
    attempt: Callable[[int], T],
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> RetryResult[T]:
    """Run ``attempt(n)`` (1-indexed), retrying only transient failures.

    A terminal failure surfaces immediately, undelayed by backoff. A transient
    failure retries up to ``max_retries`` times with exponentially increasing
    backoff between attempts.
    """
    errors: list[str] = []
    for n in range(1, max_retries + 2):
        try:
            value = attempt(n)
        except Exception as exc:  # classify, then decide whether to retry
            errors.append(str(exc))
            if is_transient(exc) and n <= max_retries:
                sleep(backoff_base * (2 ** (n - 1)))
                continue
            return RetryResult(
                value=None, succeeded=False, attempts=n, errors=errors, final_error=exc
            )
        return RetryResult(value=value, succeeded=True, attempts=n, errors=errors)
    raise AssertionError("unreachable")  # pragma: no cover


def retrying_runner(
    inner: Callable[[str], JobRecord],
    job_store: JobStore,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[str], JobRecord]:
    """Wrap a runner with the §12.2 retry policy and persist the attempt count.

    ``inner(job_id)`` must **raise** on a retryable (transient) failure and
    otherwise return a terminal :class:`JobRecord` (a terminal failure is a
    returned ``FAILED`` record — it is never retried).
    """

    def run(job_id: str) -> JobRecord:
        result = run_with_retry(
            lambda _n: inner(job_id),
            max_retries=max_retries,
            backoff_base=backoff_base,
            sleep=sleep,
        )
        annotation = {"attempts": result.attempts, "attempt_errors": result.errors}
        if result.succeeded and result.value is not None:
            return job_store.update(result.value.model_copy(update=annotation))

        current = job_store.load(job_id) or JobRecord(id=job_id, vault="")
        base = (
            current
            if current.state is JobState.RUNNING
            else current.transitioned_to(JobState.RUNNING)
        )
        failed = base.model_copy(
            update={
                **annotation,
                "failure_stage": "retry-exhausted",
                "error": str(result.final_error),
            }
        )
        return job_store.update(failed.transitioned_to(JobState.FAILED))

    return run


__all__ = ["DEFAULT_MAX_RETRIES", "RetryResult", "retrying_runner", "run_with_retry"]
