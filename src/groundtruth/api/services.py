"""Service wiring shared by the API routers (adapter support, not business logic).

Ties the registry, config, job store, source index, per-vault queue and the ingest
pipeline together so the routers stay thin.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import load_vault_config
from ..ingest.pipeline import IngestPipeline
from ..jobs.queue import JobQueue
from ..jobs.retry import retrying_runner
from ..llm.client import LLMClient
from ..models import JobRecord, JobState, Vault
from ..storage.job_store import JobStore
from ..storage.registry import VaultRegistry
from ..storage.source_index import SourceIndex
from .errors import problem


@dataclass
class _PendingIngest:
    vault: Vault
    text: str
    source_label: str


@dataclass
class Services:
    state_dir: str
    registry: VaultRegistry
    job_store: JobStore
    source_index: SourceIndex
    cli_config: Path | None = None
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    client_override: Any = None  # a stand-in LLM client for tests

    _pending: dict[str, _PendingIngest] = field(default_factory=dict, init=False)
    queue: JobQueue = field(init=False)
    pipeline: IngestPipeline = field(init=False)

    def __post_init__(self) -> None:
        self.pipeline = IngestPipeline(
            state_dir=self.state_dir, job_store=self.job_store, source_index=self.source_index
        )
        self.queue = JobQueue(self.job_store, retrying_runner(self._run_job, self.job_store))

    # --- ingest ------------------------------------------------------------

    def submit_ingest(
        self, vault_name: str, text: str, source_label: str, *, wait: bool, timeout: float
    ) -> JobRecord | str:
        vault = self.registry.get(vault_name)
        if vault is None:
            problem(422, f"vault {vault_name!r} is not registered")

        job_id = uuid.uuid4().hex
        self.job_store.create(JobRecord(id=job_id, vault=vault_name))
        self._pending[job_id] = _PendingIngest(vault=vault, text=text, source_label=source_label)
        self.queue.submit(vault_name, job_id)

        if not wait:
            return job_id
        try:
            return self.queue.wait(job_id, timeout=timeout)
        except TimeoutError:
            return job_id  # degrade to returning the id (spec §10.1)

    def get_job(self, job_id: str) -> JobRecord:
        record = self.job_store.load(job_id)
        if record is None:
            problem(404, f"no job {job_id!r}")
        return record

    # --- internals ---------------------------------------------------------

    def _run_job(self, job_id: str) -> JobRecord:
        pending = self._pending.get(job_id)
        if pending is None:  # e.g. re-queued after a restart with no in-memory request
            return self._orphan_fail(job_id)
        config = load_vault_config(
            pending.vault.name,
            cli_config=self.cli_config,
            environ=self.environ,
            repo_root=pending.vault.repo_root,
        )
        client = self.client_override or LLMClient(config.models, environ=self.environ)
        try:
            return self.pipeline.run(
                job_id=job_id,
                vault=pending.vault,
                text=pending.text,
                source_label=pending.source_label,
                config=config,
                client=client,
            )
        finally:
            self._pending.pop(job_id, None)

    def _orphan_fail(self, job_id: str) -> JobRecord:
        record = self.job_store.load(job_id)
        if record is None:
            return JobRecord(id=job_id, vault="", state=JobState.FAILED)
        base = (
            record if record.state is JobState.RUNNING else record.transitioned_to(JobState.RUNNING)
        )
        failed = base.model_copy(
            update={"failure_stage": "restart", "error": "ingest request was lost on restart"}
        )
        return self.job_store.update(failed.transitioned_to(JobState.FAILED))


__all__ = ["Services"]
