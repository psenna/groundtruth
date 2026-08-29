"""Ingest pipeline orchestrator (spec §7 end to end).

Assembles the ingestion sequence with all-or-nothing semantics. This module is
**orchestration only** - every step's logic lives in issues #8..#22. The stage
order is exactly §7:

    clean-tree -> dedup -> pull -> retrieve -> LLM -> validate -> archive
    -> stage -> commit -> push -> result

Rollback (``git reset --hard`` + ``git clean -fd``) runs on failure at any stage
**after** the clean-tree precondition passed (invariants 5 and 7, ADR-4). It is
never run before that check, because on a dirty tree it would destroy unsaved
edits.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..config import VaultConfig
from ..errors import GitConflictError, GroundtruthError, is_transient
from ..llm.client import LLMResponse
from ..models import JobRecord, JobState, Note, NoteFrontmatter, SourceRecord, Vault
from ..retrieval.agent import AgentStatus, run_agent
from ..retrieval.budget import Budget, BudgetLimits
from ..retrieval.tools import ReadOnlyTools
from ..storage.git import GitPushError, GitRepo
from ..storage.job_store import JobStore
from ..storage.notes import NoteRepository
from ..storage.source_index import SourceIndex
from .archive import set_commit_sha, write_archive
from .commit_message import format_commit_message
from .dedup import check_dedup, content_hash, mark_deduped
from .prompts import ORGANIZE, REDUCE, TAG, parse_reduced_items, parse_tags, render_prompt
from .schema import load_schema
from .validator import validate
from .vocabulary import derive_vocabulary
from .write_tools import PendingWrites, WriteTools

_SURVEY_INSTRUCTION = (
    "You are surveying an Obsidian vault to find the notes that the text below "
    "relates to, so it can be integrated without creating duplicates. Read "
    "schema.md first, then use ls/grep/read. Report the vault-relative paths of "
    "the relevant notes, or say 'none'.\n\n"
)


class _StageFailureError(Exception):
    def __init__(self, stage: str, message: str, *, rollback: bool) -> None:
        super().__init__(message)
        self.stage = stage
        self.message = message
        self.rollback = rollback


@dataclass
class _Accum:
    tokens: dict[str, int] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    current: str = "init"

    def add_tokens(self, role: str, response: LLMResponse) -> None:
        self.tokens[role] = self.tokens.get(role, 0) + response.usage.total_tokens


class IngestPipeline:
    def __init__(self, *, state_dir: str, job_store: JobStore, source_index: SourceIndex) -> None:
        self._state_dir = state_dir
        self._jobs = job_store
        self._index = source_index

    def run(
        self,
        *,
        job_id: str,
        vault: Vault,
        text: str,
        source_label: str,
        config: VaultConfig,
        client: Any,
        today: date | None = None,
    ) -> JobRecord:
        today = today or date.today()
        sha = content_hash(text)
        repo = GitRepo(vault.repo_root)
        acc = _Accum()

        prior = self._jobs.load(job_id)
        job = prior or self._jobs.create(JobRecord(id=job_id, vault=vault.name))
        job = self._jobs.update(
            job.model_copy(update={"source_sha": sha}).transitioned_to(JobState.RUNNING)
        )

        try:
            self._stage(acc, "clean-tree", lambda: self._require_clean(repo))

            hit = check_dedup(vault.name, text, self._index)
            if hit is not None:
                deduped = mark_deduped(job, hit).model_copy(
                    update={"notes_updated": list(hit.prior.notes_touched)}
                )
                return self._jobs.update(deduped.transitioned_to(JobState.SUCCEEDED))

            if config.auto_push:
                self._stage(acc, "pre-sync", lambda: self._pull(repo))

            schema = load_schema(vault.vault_dir)
            vocab = derive_vocabulary(
                vault, state_dir=self._state_dir, vocab_max_bytes=config.limits.vocab_max_bytes
            )
            note_repo = NoteRepository(vault.vault_dir)
            existing = {note.path for note in note_repo.list_notes()}

            relevant = self._stage(
                acc, "retrieval", lambda: self._survey(client, vault, config, text)
            )
            reduced = self._stage(acc, "llm", lambda: self._reduce(client, acc, schema.raw, text))
            tags = self._stage(
                acc, "llm", lambda: self._tag(client, acc, schema.raw, vocab.render(), reduced)
            )
            pending: PendingWrites = self._stage(
                acc,
                "llm",
                lambda: self._organize(
                    client,
                    acc,
                    config,
                    vault,
                    schema.raw,
                    vocab.render(),
                    relevant,
                    reduced,
                    existing,
                ),
            )

            staged = [
                note.with_frontmatter(
                    NoteFrontmatter(
                        title=note.title or note.path.rsplit("/", 1)[-1].removesuffix(".md"),
                        tags=tags,
                        sources=[sha],
                        created=today,
                        updated=today,
                    )
                )
                for note in pending
            ]
            pending.notes[:] = staged
            self._stage(
                acc,
                "write-validation",
                lambda: validate(
                    pending,
                    schema,
                    config.limits,
                    vault_root=str(vault.vault_dir),
                    existing_paths=existing,
                ),
            )

            created = [n.path for n in pending if n.is_new]
            updated = [n.path for n in pending if not n.is_new]
            touched = created + updated

            archive = write_archive(
                vault.repo_root,
                sha256=sha,
                text=text,
                source_label=source_label,
                job_id=job_id,
                ingested_at=today,
                notes_touched=touched,
                enabled=config.raw_archive,
            )

            for note in pending:
                assert isinstance(note.frontmatter, NoteFrontmatter)  # set + validated above
                note_repo.write(Note(path=note.path, frontmatter=note.frontmatter, body=note.body))

            message = format_commit_message(
                vault=vault.name,
                subject=_subject(created, updated),
                created=[_stem(p) for p in created],
                updated=[_stem(p) for p in updated],
                tags=tags,
                source_sha=sha,
                job_id=job_id,
                excerpt=text,
            )
            repo.add()
            commit_sha = repo.commit(message)
            if archive is not None:
                set_commit_sha(vault.repo_root, sha, commit_sha)
                repo.add()
                commit_sha = repo.amend()

            self._index.put(
                vault.name,
                SourceRecord(
                    sha256=sha,
                    job_id=job_id,
                    commit_sha=commit_sha,
                    notes_touched=touched,
                    ingested_at=today,
                    source_label=source_label,
                ),
            )

            push_error: str | None = None
            if config.auto_push:
                try:
                    repo.push()
                except GitPushError as exc:  # §7.10 — the ingest succeeded, the sync did not
                    push_error = str(exc)

            done = job.model_copy(
                update={
                    "commit_sha": commit_sha,
                    "notes_created": created,
                    "notes_updated": updated,
                    "token_usage": acc.tokens,
                    "stage_timings": acc.timings,
                    "error": push_error,
                }
            )
            return self._jobs.update(done.transitioned_to(JobState.SUCCEEDED))

        except _StageFailureError as failure:
            return self._fail(job, acc, repo, failure.stage, failure.message, failure.rollback)
        except GroundtruthError as exc:
            if is_transient(exc):
                # Roll the repo back to clean and let the worker's retry policy (#28)
                # decide — a transient failure may succeed on the next attempt (§12.2).
                repo.rollback()
                raise
            return self._fail(job, acc, repo, acc.current, str(exc), rollback=True)

    def _fail(
        self,
        job: JobRecord,
        acc: _Accum,
        repo: GitRepo,
        stage: str,
        message: str,
        rollback: bool,
    ) -> JobRecord:
        if rollback:
            repo.rollback()
        failed = job.model_copy(
            update={
                "failure_stage": stage,
                "error": message,
                "stage_timings": acc.timings,
                "token_usage": acc.tokens,
            }
        )
        return self._jobs.update(failed.transitioned_to(JobState.FAILED))

    # --- stages ----------------------------------------------------------------

    def _stage(self, acc: _Accum, name: str, run: Any) -> Any:
        acc.current = name
        start = time.monotonic()
        try:
            return run()
        finally:
            acc.timings[name] = acc.timings.get(name, 0.0) + (time.monotonic() - start)

    @staticmethod
    def _require_clean(repo: GitRepo) -> None:
        if not repo.is_clean():
            raise _StageFailureError(
                "clean-tree", "working tree is dirty; refusing to ingest (§7.1)", rollback=False
            )

    @staticmethod
    def _pull(repo: GitRepo) -> None:
        try:
            repo.pull_ff_only()
        except GitConflictError as exc:
            raise _StageFailureError("pre-sync", str(exc), rollback=False) from exc

    def _survey(self, client: Any, vault: Vault, config: VaultConfig, text: str) -> str:
        budget = Budget(BudgetLimits.from_limits(config.limits))
        tools = ReadOnlyTools(vault.vault_dir, budget)
        outcome = run_agent(client, REDUCE, _SURVEY_INSTRUCTION + text, tools, budget)
        if outcome.status is AgentStatus.EXHAUSTED:
            raise _StageFailureError(
                "retrieval",
                "retrieval budget exhausted; failing the job rather than risk a "
                "duplicate note (§7.4). Raise max_tool_calls for this vault.",
                rollback=True,
            )
        if outcome.status is AgentStatus.FAILED:
            raise _StageFailureError(
                "retrieval", outcome.error or "retrieval failed", rollback=True
            )
        return outcome.final_text or "none"

    def _reduce(self, client: Any, acc: _Accum, schema_md: str, text: str) -> list[str]:
        prompt = render_prompt(REDUCE, schema_md=schema_md, input_text=text)
        response = self._complete(client, REDUCE, prompt, acc)
        return parse_reduced_items(response.text or "")

    def _tag(
        self, client: Any, acc: _Accum, schema_md: str, vocab: str, reduced: list[str]
    ) -> list[str]:
        prompt = render_prompt(
            TAG, schema_md=schema_md, derived_vocabulary=vocab, input_text="\n".join(reduced)
        )
        response = self._complete(client, TAG, prompt, acc)
        return parse_tags(response.text or "")

    def _organize(
        self,
        client: Any,
        acc: _Accum,
        config: VaultConfig,
        vault: Vault,
        schema_md: str,
        vocab: str,
        relevant: str,
        reduced: list[str],
        existing: set[str],
    ) -> Any:
        budget = Budget(BudgetLimits.from_limits(config.limits))
        tools = WriteTools(vault.vault_dir, existing_paths=existing)
        prompt = render_prompt(
            ORGANIZE,
            schema_md=schema_md,
            derived_vocabulary=vocab,
            existing_notes=relevant,
            input_items="\n".join(f"- {item}" for item in reduced),
        )
        outcome = run_agent(client, ORGANIZE, prompt, tools, budget)
        if outcome.status is AgentStatus.EXHAUSTED:
            raise _StageFailureError("llm", "organize budget exhausted", rollback=True)
        if outcome.status is AgentStatus.FAILED:
            raise _StageFailureError("llm", outcome.error or "organize failed", rollback=True)
        if len(tools.pending) == 0:
            raise _StageFailureError("llm", "organize produced no writes", rollback=True)
        return tools.pending

    def _complete(self, client: Any, role: str, prompt: str, acc: _Accum) -> LLMResponse:
        try:
            response: LLMResponse = client.complete(role, [{"role": "user", "content": prompt}])
        except GroundtruthError as exc:
            if is_transient(exc):
                raise  # handled by run()'s transient branch -> worker retry (#28)
            raise _StageFailureError("llm", str(exc), rollback=True) from exc
        acc.add_tokens(role, response)
        return response


def _stem(path: str) -> str:
    return path.rsplit("/", 1)[-1].removesuffix(".md")


def _subject(created: Sequence[str], updated: Sequence[str]) -> str:
    names = [_stem(p) for p in [*created, *updated]]
    if not names:
        return "no notes"
    head = ", ".join(names[:3])
    return head if len(names) <= 3 else f"{head} +{len(names) - 3}"


__all__ = ["IngestPipeline"]
