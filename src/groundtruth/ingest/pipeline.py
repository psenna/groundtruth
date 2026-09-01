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
from ..errors import GitConflictError, GroundtruthError, MalformedLLMOutputError, is_transient
from ..llm.client import LLMResponse
from ..models import JobRecord, JobState, Note, NoteFrontmatter, SourceRecord, Vault
from ..observability import log_stage
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
from .schema import Schema, load_schema
from .validator import ValidationRejectionError, validate
from .vocabulary import derive_vocabulary
from .write_tools import PendingNote, PendingWrites, WriteTools

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
    job_id: str = ""
    vault: str = ""
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
        acc = _Accum(job_id=job_id, vault=vault.name)

        prior = self._jobs.load(job_id)
        job = prior or self._jobs.create(JobRecord(id=job_id, vault=vault.name))
        job = self._jobs.update(
            job.model_copy(
                update={"source_sha": sha, "source_bytes": len(text.encode("utf-8"))}
            ).transitioned_to(JobState.RUNNING)
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
            pending: PendingWrites = self._organize_and_validate(
                acc=acc,
                client=client,
                config=config,
                vault=vault,
                schema=schema,
                vocab=vocab.render(),
                relevant=relevant,
                reduced=reduced,
                existing=existing,
                sha=sha,
                today=today,
            )
            tags = sorted({t for n in pending for t in _frontmatter(n).tags})

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
            log_stage(
                job_id, vault.name, "job", "succeeded", commit_sha=commit_sha, tokens=acc.tokens
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
        log_stage(acc.job_id, acc.vault, stage, "failed", error=message)
        return self._jobs.update(failed.transitioned_to(JobState.FAILED))

    # --- stages ----------------------------------------------------------------

    def _stage(self, acc: _Accum, name: str, run: Any) -> Any:
        acc.current = name
        log_stage(acc.job_id, acc.vault, name, "start")
        start = time.monotonic()
        try:
            return run()
        finally:
            elapsed = time.monotonic() - start
            acc.timings[name] = acc.timings.get(name, 0.0) + elapsed
            log_stage(acc.job_id, acc.vault, name, "end", seconds=round(elapsed, 4))

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

    def _tag(self, client: Any, acc: _Accum, schema_md: str, vocab: str, text: str) -> list[str]:
        """Tag one note from its own body (§7.5). Called per note after organize,
        so a note is tagged for what it says, not for the whole source doc.
        """
        prompt = render_prompt(TAG, schema_md=schema_md, derived_vocabulary=vocab, input_text=text)
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        for attempt in range(1, _TAG_ATTEMPTS + 1):
            response = self._complete(client, TAG, messages, acc)
            try:
                return parse_tags(response.text or "")
            except MalformedLLMOutputError as exc:
                if attempt == _TAG_ATTEMPTS:
                    raise _StageFailureError("llm", str(exc), rollback=True) from exc
                log_stage(acc.job_id, acc.vault, "llm", "retry", step="tag", reason=str(exc))
                messages = [
                    *messages,
                    {"role": "assistant", "content": response.text or ""},
                    {"role": "user", "content": _TAG_RETRY.format(error=exc)},
                ]
        raise AssertionError("unreachable: the loop returns or re-raises")

    def _organize_and_validate(
        self,
        *,
        acc: _Accum,
        client: Any,
        config: VaultConfig,
        vault: Vault,
        schema: Schema,
        vocab: str,
        relevant: str,
        reduced: list[str],
        existing: set[str],
        sha: str,
        today: date,
    ) -> PendingWrites:
        """Run organize, tag each note, stamp frontmatter, validate — retrying on
        a validator rejection with the rejection fed back to the model (§7.5).

        The retry is *not* sanitize-and-continue (ADR-5): the model redoes its
        own output, every attempt goes through the same validator, and once the
        attempts are spent an invalid batch still fails the job loudly.
        """
        base_prompt = render_prompt(
            ORGANIZE,
            schema_md=schema.raw,
            derived_vocabulary=vocab,
            existing_notes=relevant,
            existing_note_paths=_note_path_listing(existing),
            input_items="\n".join(f"- {item}" for item in reduced),
        )
        attempts = max(1, config.limits.organize_max_attempts)
        conversation: str | list[dict[str, Any]] = base_prompt

        for attempt in range(1, attempts + 1):
            drafted: tuple[PendingWrites, list[dict[str, Any]]] = self._stage(
                acc,
                "llm",
                lambda convo=conversation: self._run_organize_agent(
                    client, config, vault, existing, convo, acc
                ),
            )
            pending, messages = drafted
            if len(pending) == 0:
                if attempt == attempts:
                    raise _StageFailureError("llm", "organize produced no writes", rollback=True)
                log_stage(
                    acc.job_id, acc.vault, "llm", "retry", step="organize", reason="no writes"
                )
                conversation = [*messages, {"role": "user", "content": _NO_WRITES_FEEDBACK}]
                continue
            pending.notes[:] = [
                _stamp(
                    note,
                    self._stage(
                        acc,
                        "llm",
                        lambda body=note.body: self._tag(client, acc, schema.raw, vocab, body),
                    ),
                    sha,
                    today,
                )
                for note in pending
            ]
            try:
                self._stage(
                    acc,
                    "write-validation",
                    lambda staged=pending: validate(
                        staged,
                        schema,
                        config.limits,
                        vault_root=str(vault.vault_dir),
                        existing_paths=existing,
                    ),
                )
            except ValidationRejectionError as rejection:
                if attempt == attempts:
                    raise
                log_stage(
                    acc.job_id,
                    acc.vault,
                    "write-validation",
                    "retry",
                    attempt=attempt,
                    remaining=attempts - attempt,
                    rule=rejection.rule,
                    note=rejection.note_path,
                )
                conversation = [
                    *messages,
                    {"role": "user", "content": _retry_feedback(rejection, schema)},
                ]
                continue
            return pending

        raise AssertionError("unreachable: the loop returns or re-raises")

    def _run_organize_agent(
        self,
        client: Any,
        config: VaultConfig,
        vault: Vault,
        existing: set[str],
        conversation: str | list[dict[str, Any]],
        acc: _Accum,
    ) -> tuple[PendingWrites, list[dict[str, Any]]]:
        budget = Budget(BudgetLimits.from_limits(config.limits))
        tools = WriteTools(
            vault.vault_dir,
            existing_paths=existing,
            budget=budget,
            job_id=acc.job_id,
            vault_name=vault.name,
        )
        outcome = run_agent(client, ORGANIZE, conversation, tools, budget)
        if outcome.status is AgentStatus.EXHAUSTED:
            raise _StageFailureError("llm", "organize budget exhausted", rollback=True)
        if outcome.status is AgentStatus.FAILED:
            raise _StageFailureError("llm", outcome.error or "organize failed", rollback=True)
        return tools.pending, outcome.messages

    def _complete(
        self, client: Any, role: str, prompt: str | list[dict[str, Any]], acc: _Accum
    ) -> LLMResponse:
        messages = [{"role": "user", "content": prompt}] if isinstance(prompt, str) else prompt
        try:
            response: LLMResponse = client.complete(role, messages)
        except GroundtruthError as exc:
            if is_transient(exc):
                raise  # handled by run()'s transient branch -> worker retry (#28)
            raise _StageFailureError("llm", str(exc), rollback=True) from exc
        acc.add_tokens(role, response)
        return response


def _stem(path: str) -> str:
    return path.rsplit("/", 1)[-1].removesuffix(".md")


def _note_path_listing(existing: set[str]) -> str:
    if not existing:
        return "(the vault has no notes yet)"
    return "\n".join(f"- {p}" for p in sorted(existing)[:1000])


def _stamp(note: PendingNote, tags: list[str], sha: str, today: date) -> PendingNote:
    return note.with_frontmatter(
        NoteFrontmatter(
            title=note.title or _stem(note.path),
            tags=tags,
            sources=[sha],
            created=today,
            updated=today,
        )
    )


def _frontmatter(note: PendingNote) -> NoteFrontmatter:
    assert isinstance(note.frontmatter, NoteFrontmatter)  # stamped before this point
    return note.frontmatter


#: Rule-specific nudge appended to the generic retry feedback.
_RETRY_HINTS: dict[str, str] = {
    "link_integrity": (
        "A [[wikilink]] may point only at a note listed under 'Every note path in "
        "the vault' or one you create in this same batch. Write every other "
        "reference as plain text, not a link."
    ),
    "collision": (
        "That path already exists — call update_note(path, body) for it instead of create_note."
    ),
    "missing_target": (
        "That path does not exist yet — call create_note(folder, title, body) "
        "instead of update_note."
    ),
    "folder": (
        "Create notes only in a folder that is declared verbatim in the schema — "
        "do not add a sub-folder of your own (`projects/x/ci/` is not allowed if "
        "only `projects/x/` is declared). Put the note in the closest declared "
        "folder instead."
    ),
    "note_count": (
        "Too many notes touched. Consolidate to one note per topic or entity "
        "(never one per claim) and update existing notes instead of adding "
        "near-duplicates."
    ),
    "duplicate_path": "You staged the same path twice — merge those into one call.",
    "filename": ("Keep the title plain: no slashes, no leading dots, no path segments."),
    "note_substance": (
        "You created a note but left its body a stub ('placeholder', a lone "
        "heading, only links). Write the full note body in the same create_note "
        "call, or do not create that note at all — there is no second pass."
    ),
}


#: Tag-step attempts (the first, plus one re-prompt with the parse error).
_TAG_ATTEMPTS = 2

_TAG_RETRY = (
    "That was not a usable tag list: {error}. Reply with ONLY the tags — one per "
    "line, lowercase and hyphen-separated, no prose, no numbering, no parentheses."
)

_NO_WRITES_FEEDBACK = (
    "You finished without calling create_note or update_note, so nothing would be "
    "saved. Every ingest must write at least one note. Go through the distilled "
    "items again and call the write tools now."
)


def _retry_feedback(rejection: ValidationRejectionError, schema: Schema | None = None) -> str:
    hint = _RETRY_HINTS.get(rejection.rule, "")
    if rejection.rule == "folder" and schema is not None:
        allowed = ", ".join(sorted(schema.folders)) or "(none)"
        hint = f"{hint}\nThe only folders you may use: {allowed}"
    return (
        f"STOP — the validator rejected the notes and nothing was saved:\n\n"
        f"    {rejection}\n\n"
        f"{hint}\n\n"
        "Redo the ENTIRE batch of create_note / update_note calls from scratch — "
        "the previous calls were all discarded. Do not repeat the rejected output."
    ).strip()


def _subject(created: Sequence[str], updated: Sequence[str]) -> str:
    names = [_stem(p) for p in [*created, *updated]]
    if not names:
        return "no notes"
    head = ", ".join(names[:3])
    return head if len(names) <= 3 else f"{head} +{len(names) - 3}"


__all__ = ["IngestPipeline"]
