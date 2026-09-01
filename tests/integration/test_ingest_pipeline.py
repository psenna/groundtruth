from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest

from groundtruth.config import Limits, ModelConfig, VaultConfig
from groundtruth.errors import MalformedLLMOutputError
from groundtruth.ingest.dedup import content_hash
from groundtruth.ingest.pipeline import IngestPipeline
from groundtruth.llm.client import LLMResponse, TokenUsage, ToolCall
from groundtruth.models import JobState, Note, NoteFrontmatter, SourceRecord, Vault
from groundtruth.storage.frontmatter import parse_note, render_note
from groundtruth.storage.git import GitRepo
from groundtruth.storage.job_store import JobStore
from groundtruth.storage.source_index import SourceIndex

pytestmark = pytest.mark.integration

TEXT = "Acme Corp was founded in 1996 and ships the Widget Platform. It is a vendor.\n"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _config(
    *,
    auto_push: bool = False,
    raw_archive: bool = True,
    max_tool_calls: int = 30,
    organize_max_attempts: int = 2,
) -> VaultConfig:
    return VaultConfig(
        raw_archive=raw_archive,
        auto_push=auto_push,
        allow_schema_writes=False,
        models={"default": ModelConfig(base_url="http://x/v1", model="m", api_key_env="K")},
        limits=Limits(
            max_notes_per_ingest=10,
            max_note_bytes=65536,
            max_tool_calls=max_tool_calls,
            max_wall_clock_s=60,
            grep_max_matches=50,
            grep_max_bytes=65536,
            read_max_bytes=32768,
            vocab_max_bytes=4096,
            organize_max_attempts=organize_max_attempts,
        ),
    )


class ScriptedClient:
    def __init__(self, responses: list[LLMResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def complete(self, role: str, messages: object, **kw: object) -> LLMResponse:
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _text(content: str, role: str = "x") -> LLMResponse:
    return LLMResponse(role=role, model="m", text=content, usage=TokenUsage(3, 2, 5))


def _tool(name: str, args: dict[str, str]) -> LLMResponse:
    return LLMResponse(
        role="organize",
        model="m",
        text=None,
        tool_calls=[ToolCall(id="c", name=name, arguments=args)],
        usage=TokenUsage(3, 2, 5),
    )


def _happy_responses() -> list[LLMResponse]:
    return [
        _text("none"),  # survey
        _text("- Acme Corp was founded in 1996.\n- Acme ships Widget Platform."),  # reduce
        _tool(
            "create_note", {"folder": "companies", "title": "Acme Corp", "body": "Founded 1996."}
        ),
        _text("done"),  # organize finishes
        _text("company\nvendor"),  # tag — per note, after organize
    ]


@pytest.fixture
def env(tmp_path: Path) -> tuple[IngestPipeline, Vault, Path]:
    repo = tmp_path / "repo"
    vdir = repo / "work"
    vdir.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (vdir / "schema.md").write_text("# Schema\n\n## Folders\n- companies/\n- people/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "seed")
    state = tmp_path / "state"
    pipeline = IngestPipeline(
        state_dir=str(state), job_store=JobStore(state), source_index=SourceIndex(state)
    )
    return pipeline, Vault(name="work", repo_root=repo), state


def _run(pipeline: IngestPipeline, vault: Vault, client: object, cfg: VaultConfig, **kw: object):
    return pipeline.run(
        job_id="01JOB",
        vault=vault,
        text=TEXT,
        source_label="acme.txt",
        config=cfg,
        client=client,
        today=date(2026, 8, 1),
        **kw,
    )


class TestFailurePaths:
    def test_dirty_tree_fails_immediately_and_changes_nothing(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, _ = env
        (vault.vault_dir / "unsaved.md").write_text("a user's unsaved edit\n")
        head_before = _git(vault.repo_root, "rev-parse", "HEAD")

        client = ScriptedClient([])  # must never be called
        job = _run(pipeline, vault, client, _config())

        assert job.state is JobState.FAILED
        assert job.failure_stage == "clean-tree"
        assert client.calls == 0
        assert _git(vault.repo_root, "rev-parse", "HEAD") == head_before
        # the unsaved edit was NOT clobbered by a rollback
        assert (vault.vault_dir / "unsaved.md").read_text() == "a user's unsaved edit\n"

    def test_dedup_hit_short_circuits_without_llm_or_commit(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, state = env
        SourceIndex(state).put(
            vault.name,
            SourceRecord(
                sha256=content_hash(TEXT),
                job_id="01PRIOR",
                commit_sha="c0ffee",
                notes_touched=["companies/Acme Corp.md"],
                ingested_at=date(2026, 1, 1),
            ),
        )
        head_before = _git(vault.repo_root, "rev-parse", "HEAD")
        client = ScriptedClient([])

        job = _run(pipeline, vault, client, _config())

        assert job.state is JobState.SUCCEEDED
        assert job.dedup_of == "01PRIOR"
        assert client.calls == 0
        assert _git(vault.repo_root, "rev-parse", "HEAD") == head_before

    def test_pull_conflict_aborts_with_no_changes(
        self, env: tuple[IngestPipeline, Vault, Path], tmp_path: Path
    ) -> None:
        pipeline, vault, _ = env
        origin = tmp_path / "origin"
        _git(tmp_path, "clone", str(vault.repo_root), str(origin))
        _git(origin, "config", "user.email", "o@o")
        _git(origin, "config", "user.name", "o")
        (origin / "work" / "up.md").write_text("upstream change\n")
        _git(origin, "add", "-A")
        _git(origin, "commit", "-m", "upstream")
        _git(vault.repo_root, "remote", "add", "origin", str(origin))
        # diverge locally
        (vault.vault_dir / "local.md").write_text("local\n")
        _git(vault.repo_root, "add", "-A")
        _git(vault.repo_root, "commit", "-m", "local")
        head_before = _git(vault.repo_root, "rev-parse", "HEAD")

        job = _run(pipeline, vault, ScriptedClient([]), _config(auto_push=True))

        assert job.state is JobState.FAILED
        assert job.failure_stage == "pre-sync"
        assert _git(vault.repo_root, "rev-parse", "HEAD") == head_before
        assert GitRepo(vault.repo_root).is_clean()

    def test_validator_rejection_rolls_back_nothing_committed(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, _ = env
        head_before = _git(vault.repo_root, "rev-parse", "HEAD")
        responses = _happy_responses()
        responses[2] = _tool("create_note", {"folder": "undeclared", "title": "X", "body": "hi"})
        job = _run(pipeline, vault, ScriptedClient(responses), _config(organize_max_attempts=1))

        assert job.state is JobState.FAILED
        assert job.failure_stage == "write-validation"
        assert _git(vault.repo_root, "rev-parse", "HEAD") == head_before
        assert GitRepo(vault.repo_root).is_clean()
        assert not (vault.vault_dir / "companies").exists()

    def test_llm_failure_midrun_rolls_back_byte_identical(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, _ = env
        before = _git(vault.repo_root, "rev-parse", "HEAD")
        client = ScriptedClient([_text("none"), MalformedLLMOutputError("bad reduce output")])

        job = _run(pipeline, vault, client, _config())

        assert job.state is JobState.FAILED
        assert job.failure_stage == "llm"
        assert _git(vault.repo_root, "rev-parse", "HEAD") == before
        assert GitRepo(vault.repo_root).is_clean()

    def test_retrieval_budget_exhausted_fails_the_job(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, _ = env
        # survey keeps calling a tool; max_tool_calls=1 -> exhausted
        client = ScriptedClient([_tool("grep", {"pattern": "acme"})] * 3)

        job = _run(pipeline, vault, client, _config(max_tool_calls=1))

        assert job.state is JobState.FAILED
        assert job.failure_stage == "retrieval"
        assert GitRepo(vault.repo_root).is_clean()
        assert not (vault.vault_dir / "companies").exists()

    def test_survey_tokens_are_recorded_even_when_retrieval_exhausts(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, _ = env
        client = ScriptedClient([_tool("grep", {"pattern": "acme"})] * 3)
        job = _run(pipeline, vault, client, _config(max_tool_calls=1))

        assert job.state is JobState.FAILED
        assert job.failure_stage == "retrieval"
        # the one survey model call was still metered before the job failed
        assert job.token_usage["survey"].total_tokens == 5

    def test_organize_budget_exhausted_fails_the_job(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, _ = env
        # a runaway organize agent: keeps calling create_note, never stops.
        # WriteTools charges the budget, so max_tool_calls bounds it.
        responses = [
            _text("none"),
            _text("- Acme Corp was founded in 1996."),
            *[
                _tool("create_note", {"folder": "companies", "title": f"N{i}", "body": "x"})
                for i in range(6)
            ],
        ]
        job = _run(pipeline, vault, ScriptedClient(responses), _config(max_tool_calls=3))

        assert job.state is JobState.FAILED
        assert job.failure_stage == "llm"
        assert "budget exhausted" in (job.error or "")
        assert GitRepo(vault.repo_root).is_clean()


class TestHappyPath:
    def test_one_commit_with_notes_and_archive(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, state = env
        commits_before = int(_git(vault.repo_root, "rev-list", "--count", "HEAD"))

        job = _run(pipeline, vault, ScriptedClient(_happy_responses()), _config())

        assert job.state is JobState.SUCCEEDED
        commits_after = int(_git(vault.repo_root, "rev-list", "--count", "HEAD"))
        assert commits_after == commits_before + 1  # exactly one commit

        files = _git(vault.repo_root, "show", "--name-only", "--format=", "HEAD").splitlines()
        assert "work/companies/Acme Corp.md" in files
        sha = content_hash(TEXT)
        assert f"external/{sha}.txt" in files
        assert f"external/{sha}.json" in files

        assert job.commit_sha == _git(vault.repo_root, "rev-parse", "HEAD")
        assert job.notes_created == ["companies/Acme Corp.md"]
        assert job.token_usage  # §7.11 token counts populated
        assert job.stage_timings

        rec = SourceIndex(state).get(vault.name, sha)
        assert rec is not None and rec.commit_sha == job.commit_sha
        assert GitRepo(vault.repo_root).is_clean()

    def test_token_usage_is_tracked_per_stage_including_survey_and_organize(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        from groundtruth.models import TokenCounts

        pipeline, vault, _ = env
        job = _run(pipeline, vault, ScriptedClient(_happy_responses()), _config())

        assert job.state is JobState.SUCCEEDED
        # every _text/_tool response carries TokenUsage(3, 2, 5)
        assert set(job.token_usage) == {"survey", "reduce", "organize", "tag"}
        assert job.token_usage["survey"] == TokenCounts(
            prompt_tokens=3, completion_tokens=2, total_tokens=5
        )
        # organize made two model calls (create_note, then "done")
        assert job.token_usage["organize"] == TokenCounts(
            prompt_tokens=6, completion_tokens=4, total_tokens=10
        )
        assert job.token_usage["reduce"].total_tokens == 5
        assert job.token_usage["tag"].total_tokens == 5

    def test_commit_message_is_7_9_format(self, env: tuple[IngestPipeline, Vault, Path]) -> None:
        pipeline, vault, _ = env
        _run(pipeline, vault, ScriptedClient(_happy_responses()), _config())
        msg = _git(vault.repo_root, "log", "-1", "--format=%B")
        assert msg.startswith("ingest(work): ")
        assert "source:  sha256:" in msg
        assert "job:     01JOB" in msg

    def test_raw_archive_off_writes_no_external_but_indexes(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, state = env
        job = _run(pipeline, vault, ScriptedClient(_happy_responses()), _config(raw_archive=False))
        assert job.state is JobState.SUCCEEDED
        assert not (vault.repo_root / "external").exists()
        assert SourceIndex(state).get(vault.name, content_hash(TEXT)) is not None

    def test_push_failure_keeps_commit_and_reports_success(
        self, env: tuple[IngestPipeline, Vault, Path], tmp_path: Path
    ) -> None:
        pipeline, vault, _ = env
        origin = tmp_path / "origin"
        _git(tmp_path, "clone", str(vault.repo_root), str(origin))
        _git(vault.repo_root, "remote", "add", "origin", str(origin))
        _git(vault.repo_root, "fetch", "origin")
        _git(vault.repo_root, "branch", "--set-upstream-to=origin/main", "main")

        job = _run(pipeline, vault, ScriptedClient(_happy_responses()), _config(auto_push=True))

        assert job.state is JobState.SUCCEEDED  # §7.10 — ingest succeeded, sync did not
        assert job.error and "push" in job.error.lower()
        assert job.commit_sha == _git(vault.repo_root, "rev-parse", "HEAD")


class RecordingClient(ScriptedClient):
    """A ScriptedClient that keeps every ``messages`` array it was handed."""

    def __init__(self, responses: list[LLMResponse | Exception]) -> None:
        super().__init__(responses)
        self.seen: list[list[dict[str, object]]] = []

    def complete(self, role: str, messages: object, **kw: object) -> LLMResponse:
        self.seen.append([dict(m) for m in messages])  # type: ignore[arg-type]
        return super().complete(role, messages, **kw)


def _organize_turn(body: str) -> list[LLMResponse]:
    """One organize attempt: a single create_note call, then the model stops."""
    return [
        _tool("create_note", {"folder": "companies", "title": "Acme Corp", "body": body}),
        _text("done"),
    ]


class TestOrganizeRetry:
    def test_dangling_link_is_retried_with_feedback_then_succeeds(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, _ = env
        responses = [
            _text("none"),  # survey
            _text("- Acme Corp was founded in 1996."),  # reduce
            *_organize_turn("Founded 1996. See [[people/Nobody]]."),  # attempt 1 — dangling
            _text("company"),  # tag attempt-1's note
            *_organize_turn("Founded 1996."),  # attempt 2 — clean
            _text("company"),  # tag attempt-2's note
        ]
        client = RecordingClient(responses)
        job = _run(pipeline, vault, client, _config())

        assert job.state is JobState.SUCCEEDED
        assert job.notes_created == ["companies/Acme Corp.md"]
        assert job.link_downgrades == {}  # the retry fixed it; no downgrade needed

        fed_back = " ".join(str(m.get("content") or "") for msgs in client.seen for m in msgs)
        assert "link_integrity" in fed_back  # the rule
        assert "people/Nobody" in fed_back  # the offending target

    def test_retry_exhausted_fails_loudly_and_rolls_back(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        # An undeclared folder is not the one downgradeable failure (§7.6), so
        # exhausting the retries on it still fails the job loudly.
        pipeline, vault, _ = env
        head_before = _git(vault.repo_root, "rev-parse", "HEAD")
        turn = [
            _tool("create_note", {"folder": "undeclared", "title": "X", "body": "Founded 1996."}),
            _text("done"),
        ]
        responses = [
            _text("none"),
            _text("- Acme Corp was founded in 1996."),
            *turn,  # attempt 1
            _text("company"),  # tag
            *turn,  # attempt 2 — same
            _text("company"),  # tag
        ]
        job = _run(pipeline, vault, ScriptedClient(responses), _config())

        assert job.state is JobState.FAILED
        assert job.failure_stage == "write-validation"
        assert "folder" in (job.error or "")
        assert _git(vault.repo_root, "rev-parse", "HEAD") == head_before
        assert GitRepo(vault.repo_root).is_clean()
        assert not (vault.vault_dir / "undeclared").exists()

    def test_max_attempts_one_disables_the_retry(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, _ = env
        responses = [
            _text("none"),
            _text("- Acme Corp was founded in 1996."),
            _tool("create_note", {"folder": "undeclared", "title": "X", "body": "Founded 1996."}),
            _text("done"),
            _text("company"),  # tag
        ]
        client = ScriptedClient(responses)
        job = _run(pipeline, vault, client, _config(organize_max_attempts=1))

        assert job.state is JobState.FAILED
        assert job.failure_stage == "write-validation"
        assert client.calls == 5  # survey, reduce, organize turn (tool + done), tag — no retry

    def test_organize_no_writes_is_retried_then_succeeds(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, _ = env
        responses = [
            _text("none"),
            _text("- Acme Corp was founded in 1996."),
            _text("I don't think anything needs writing."),  # organize attempt 1 — no tool call
            *_organize_turn("Founded 1996."),  # attempt 2 — writes
            _text("company"),  # tag attempt-2's note
        ]
        client = RecordingClient(responses)
        job = _run(pipeline, vault, client, _config())

        assert job.state is JobState.SUCCEEDED
        assert job.notes_created == ["companies/Acme Corp.md"]
        fed_back = " ".join(str(m.get("content") or "") for msgs in client.seen for m in msgs)
        assert "must write at least one note" in fed_back

    def test_organize_no_writes_exhausted_fails(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, _ = env
        responses = [
            _text("none"),
            _text("- f"),
            _text("nothing to do"),  # attempt 1
            _text("still nothing"),  # attempt 2
        ]
        job = _run(pipeline, vault, ScriptedClient(responses), _config())

        assert job.state is JobState.FAILED
        assert job.failure_stage == "llm"
        assert "no writes" in (job.error or "")
        assert GitRepo(vault.repo_root).is_clean()

    def test_retry_attempts_share_one_budget_and_exhaust_it(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        # #112: the organize budget is one meter for the whole step, not a fresh
        # allowance per retry attempt. With max_tool_calls=2 the first two
        # attempts each spend one create_note; the third finds the budget spent
        # before it can call the model and the job fails loudly.
        pipeline, vault, _ = env
        head_before = _git(vault.repo_root, "rev-parse", "HEAD")
        dangling = "Founded 1996. See [[people/Nobody]]."
        responses = [
            _text("none"),  # survey
            _text("- Acme Corp was founded in 1996."),  # reduce
            *_organize_turn(dangling),  # attempt 1 — spends tool call 1
            _text("company"),  # tag
            *_organize_turn(dangling),  # attempt 2 — spends tool call 2
            _text("company"),  # tag
            *_organize_turn(dangling),  # attempt 3 — never reached
            _text("company"),
        ]
        client = ScriptedClient(responses)
        job = _run(
            pipeline,
            vault,
            client,
            _config(max_tool_calls=2, organize_max_attempts=3),
        )

        assert job.state is JobState.FAILED
        assert job.failure_stage == "llm"
        assert "organize budget exhausted" in (job.error or "")
        assert client.calls == 6  # 2 attempts x (tool + done + tag); attempt 3 never calls
        assert _git(vault.repo_root, "rev-parse", "HEAD") == head_before
        assert GitRepo(vault.repo_root).is_clean()
        assert not (vault.vault_dir / "companies").exists()


class TestTerminalLinkDowngrade:
    """§7.6 (#118): on the final organize attempt, a lone dangling wikilink is
    downgraded to plain text instead of failing the job."""

    def _responses(self, body: str) -> list[LLMResponse]:
        return [
            _text("none"),  # survey
            _text("- Acme Corp was founded in 1996."),  # reduce
            _tool("create_note", {"folder": "companies", "title": "Acme Corp", "body": body}),
            _text("done"),
            _text("company"),  # tag
        ]

    def test_lone_dangling_link_on_final_attempt_is_downgraded_and_job_succeeds(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, _ = env
        responses = self._responses("Founded 1996. See [[people/Nobody]] for more.")
        job = _run(pipeline, vault, ScriptedClient(responses), _config(organize_max_attempts=1))

        assert job.state is JobState.SUCCEEDED
        assert job.notes_created == ["companies/Acme Corp.md"]
        assert job.link_downgrades == {"companies/Acme Corp.md": ["people/Nobody"]}
        note = (vault.vault_dir / "companies" / "Acme Corp.md").read_text()
        assert "See people/Nobody for more." in note
        assert "[[" not in note

    def test_downgrade_keeps_the_alias_text_when_the_link_had_one(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, _ = env
        responses = self._responses("Founded 1996 by [[people/Nobody|its founder]].")
        job = _run(pipeline, vault, ScriptedClient(responses), _config(organize_max_attempts=1))

        assert job.state is JobState.SUCCEEDED
        note = (vault.vault_dir / "companies" / "Acme Corp.md").read_text()
        assert "Founded 1996 by its founder." in note

    def test_link_failure_plus_another_rule_on_the_final_attempt_still_fails_loudly(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, _ = env
        head_before = _git(vault.repo_root, "rev-parse", "HEAD")
        responses = [
            _text("none"),
            _text("- Acme Corp was founded in 1996."),
            _tool(
                "create_note",
                {"folder": "undeclared", "title": "X", "body": "See [[people/Nobody]]."},
            ),
            _text("done"),
            _text("company"),  # tag
        ]
        job = _run(pipeline, vault, ScriptedClient(responses), _config(organize_max_attempts=1))

        assert job.state is JobState.FAILED
        assert job.failure_stage == "write-validation"
        assert job.link_downgrades == {}
        assert _git(vault.repo_root, "rev-parse", "HEAD") == head_before
        assert GitRepo(vault.repo_root).is_clean()

    def test_dangling_link_on_a_non_final_attempt_still_takes_the_retry_path(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, _ = env
        responses = [
            _text("none"),
            _text("- Acme Corp was founded in 1996."),
            *_organize_turn("Founded 1996. See [[people/Nobody]]."),  # attempt 1 — dangling
            _text("company"),  # tag
            *_organize_turn("Founded 1996."),  # attempt 2 — clean
            _text("company"),  # tag
        ]
        client = RecordingClient(responses)
        job = _run(pipeline, vault, client, _config(organize_max_attempts=2))

        assert job.state is JobState.SUCCEEDED
        assert job.link_downgrades == {}  # attempt 1 retried, not downgraded
        assert client.calls == 8  # both attempts fully ran
        note = (vault.vault_dir / "companies" / "Acme Corp.md").read_text()
        assert "[[people/Nobody]]" not in note  # the retry produced clean output


class TestTagRetry:
    def test_unparseable_tags_are_retried_with_the_error(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, _ = env
        responses = [
            _text("none"),
            _text("- Acme Corp was founded in 1996."),
            *_organize_turn("Founded 1996."),
            _text("kubernetes (implied but not explicit — let's stick to explicit tech)"),  # bad
            _text("company"),  # retry — clean
        ]
        client = RecordingClient(responses)
        job = _run(pipeline, vault, client, _config())

        assert job.state is JobState.SUCCEEDED
        fed_back = " ".join(str(m.get("content") or "") for msgs in client.seen for m in msgs)
        assert "not a usable tag list" in fed_back

    def test_persistently_bad_tags_fail_the_job_at_llm(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, _ = env
        responses = [
            _text("none"),
            _text("- f"),
            *_organize_turn("Founded 1996."),  # a note to tag
            _text("Tag: kubernetes (maybe?)"),  # tag attempt 1 — bad
            _text("still: not a tag list"),  # tag attempt 2 — bad
        ]
        job = _run(pipeline, vault, ScriptedClient(responses), _config())

        assert job.state is JobState.FAILED
        assert job.failure_stage == "llm"
        assert GitRepo(vault.repo_root).is_clean()


def _seed_note(vault: Vault, rel: str, body: str) -> None:
    stem = rel.rsplit("/", 1)[-1].removesuffix(".md")
    note = Note(
        path=rel,
        frontmatter=NoteFrontmatter(
            title=stem,
            tags=["company"],
            sources=["b" * 64],
            created=date(2026, 7, 1),
            updated=date(2026, 7, 1),
        ),
        body=body,
    )
    target = vault.vault_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_note(note))
    _git(vault.repo_root, "add", "-A")
    _git(vault.repo_root, "commit", "-m", "seed note")


class TestCollisionCoercion:
    def test_create_over_an_existing_note_commits_as_an_update(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, _ = env
        _seed_note(vault, "companies/Acme Corp.md", "Old.\n")
        commits_before = int(_git(vault.repo_root, "rev-list", "--count", "HEAD"))

        client = RecordingClient(_happy_responses())
        job = _run(pipeline, vault, client, _config())

        assert job.state is JobState.SUCCEEDED
        assert job.notes_updated == ["companies/Acme Corp.md"]
        assert job.notes_created == []

        commits_after = int(_git(vault.repo_root, "rev-list", "--count", "HEAD"))
        assert commits_after == commits_before + 1
        files = _git(vault.repo_root, "show", "--name-only", "--format=", "HEAD").splitlines()
        assert "work/companies/Acme Corp.md" in files

        persisted = parse_note((vault.vault_dir / "companies" / "Acme Corp.md").read_text())
        assert "Founded 1996." in persisted.body
        assert persisted.frontmatter.created == date(2026, 7, 1)
        assert set(persisted.frontmatter.sources) == {"b" * 64, content_hash(TEXT)}

        fed_back = " ".join(str(m.get("content") or "") for msgs in client.seen for m in msgs)
        assert "collision" not in fed_back

    def test_a_genuinely_new_note_is_still_created(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, _ = env
        job = _run(pipeline, vault, RecordingClient(_happy_responses()), _config())

        assert job.state is JobState.SUCCEEDED
        assert job.notes_created == ["companies/Acme Corp.md"]
        assert job.notes_updated == []


class TestPerNoteTagging:
    def test_each_note_is_tagged_from_its_own_body(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        pipeline, vault, _ = env
        responses = [
            _text("none"),  # survey
            _text("- Acme was founded in 1996.\n- Bob is the CEO."),  # reduce
            _tool("create_note", {"folder": "companies", "title": "Acme", "body": "Founded 1996."}),
            _tool("create_note", {"folder": "people", "title": "Bob", "body": "CEO of Acme."}),
            _text("done"),
            _text("company\nvendor"),  # tag for Acme
            _text("person"),  # tag for Bob — different note, different tags
        ]
        job = _run(pipeline, vault, ScriptedClient(responses), _config())

        assert job.state is JobState.SUCCEEDED
        acme = (vault.vault_dir / "companies" / "Acme.md").read_text()
        bob = (vault.vault_dir / "people" / "Bob.md").read_text()
        assert "tags: [company, vendor]" in acme
        assert "tags: [person]" in bob

    def test_the_tag_prompt_carries_the_notes_own_path(
        self, env: tuple[IngestPipeline, Vault, Path]
    ) -> None:
        # #115: the per-note tag call is given the note's own path so its tags
        # stay anchored to its home topic.
        pipeline, vault, _ = env
        client = RecordingClient(_happy_responses())
        job = _run(pipeline, vault, client, _config())

        assert job.state is JobState.SUCCEEDED
        assert "companies/Acme Corp.md" in str(client.seen[-1])  # the tag call
