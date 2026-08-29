"""Per-vault FIFO job queue and worker (spec §4.4).

One ingest at a time within a vault — this serialization is what makes the git
and filesystem mutations safe, and it is a correctness requirement, not a
performance choice. Jobs on different vaults run concurrently.

Each vault has its own worker thread draining its own FIFO deque. A failing job
never blocks its queue: the runner returns a terminal ``JobRecord`` and the
worker moves on.
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

from ..models import JobRecord, JobState
from ..storage.job_store import JobStore

#: Executes one job to a terminal state and returns its record.
Runner = Callable[[str], JobRecord]


@dataclass(frozen=True)
class QueuePosition:
    vault: str
    #: 0 while the job is running or is next; ``None`` once it has left the queue.
    position: int | None
    depth: int


class JobQueue:
    """Dispatches submitted jobs to per-vault workers."""

    def __init__(self, job_store: JobStore, runner: Runner) -> None:
        self._store = job_store
        self._runner = runner
        self._lock = threading.Lock()
        self._pending: dict[str, deque[str]] = defaultdict(deque)
        self._running: dict[str, str | None] = defaultdict(lambda: None)
        self._workers: dict[str, threading.Thread] = {}
        self._done: dict[str, threading.Event] = {}
        self._results: dict[str, JobRecord] = {}
        self._vault_of: dict[str, str] = {}
        self._stopping = threading.Event()

    # --- submission ----------------------------------------------------------

    def submit(self, vault: str, job_id: str) -> str:
        """Enqueue ``job_id`` for ``vault`` and return it immediately (``wait=false``)."""
        with self._lock:
            if self._stopping.is_set():
                raise RuntimeError("queue is shutting down")
            self._pending[vault].append(job_id)
            self._done[job_id] = threading.Event()
            self._vault_of[job_id] = vault
            self._ensure_worker(vault)
        return job_id

    def submit_and_wait(
        self, vault: str, job_id: str, *, timeout: float | None = None
    ) -> JobRecord:
        """Enqueue ``job_id`` and block until it reaches a terminal state (``wait=true``)."""
        self.submit(vault, job_id)
        return self.wait(job_id, timeout=timeout)

    def wait(self, job_id: str, *, timeout: float | None = None) -> JobRecord:
        event = self._done.get(job_id)
        if event is None:
            raise KeyError(job_id)
        if not event.wait(timeout):
            raise TimeoutError(f"job {job_id} did not finish within {timeout}s")
        return self._results[job_id]

    # --- observability -----------------------------------------------------

    def position_of(self, job_id: str) -> QueuePosition:
        with self._lock:
            vault = self._vault_of.get(job_id)
            if vault is None:
                return QueuePosition(vault="", position=None, depth=0)
            queued = list(self._pending[vault])
            depth = len(queued) + (1 if self._running[vault] else 0)
            if self._running[vault] == job_id:
                return QueuePosition(vault=vault, position=0, depth=depth)
            if job_id in queued:
                return QueuePosition(vault=vault, position=queued.index(job_id) + 1, depth=depth)
            return QueuePosition(vault=vault, position=None, depth=depth)

    def depth(self, vault: str) -> int:
        with self._lock:
            return len(self._pending[vault]) + (1 if self._running[vault] else 0)

    # --- shutdown --------------------------------------------------------

    def shutdown(self, *, drain: bool, timeout: float | None = None) -> None:
        """Stop the queue. ``drain`` finishes every queued job; otherwise queued
        jobs are left ``QUEUED`` in the store and only the in-flight job completes.
        """
        if not drain:
            self._stopping.set()
        with self._lock:
            workers = list(self._workers.values())
        for worker in workers:
            worker.join(timeout)

    # --- internals -----------------------------------------------------

    def _ensure_worker(self, vault: str) -> None:
        worker = self._workers.get(vault)
        if worker is None or not worker.is_alive():
            worker = threading.Thread(target=self._work, args=(vault,), daemon=True)
            self._workers[vault] = worker
            worker.start()

    def _work(self, vault: str) -> None:
        while True:
            with self._lock:
                if self._stopping.is_set() or not self._pending[vault]:
                    self._workers.pop(vault, None)
                    return
                job_id = self._pending[vault].popleft()
                self._running[vault] = job_id
            try:
                result = self._runner(job_id)
            except Exception as exc:  # a runner should not raise, but never wedge the queue
                result = self._force_fail(job_id, str(exc))
            with self._lock:
                self._running[vault] = None
                self._results[job_id] = result
            self._done[job_id].set()

    def _force_fail(self, job_id: str, message: str) -> JobRecord:
        existing = self._store.load(job_id) or JobRecord(id=job_id, vault=self._vault_of[job_id])
        target = (
            existing
            if existing.state is JobState.RUNNING
            else existing.transitioned_to(JobState.RUNNING)
        )
        failed = target.model_copy(update={"failure_stage": "worker", "error": message})
        return self._store.update(failed.transitioned_to(JobState.FAILED))


__all__ = ["JobQueue", "QueuePosition", "Runner"]
