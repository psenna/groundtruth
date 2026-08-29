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
from groundtruth.models import JobState, SourceRecord, Vault
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
    *, auto_push: bool = False, raw_archive: bool = True, max_tool_calls: int = 30
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
        _text("company\nvendor"),  # tag
        _tool(
            "create_note", {"folder": "companies", "title": "Acme Corp", "body": "Founded 1996."}
        ),
        _text("done"),  # organize finishes
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
        responses[3] = _tool("create_note", {"folder": "undeclared", "title": "X", "body": "hi"})
        job = _run(pipeline, vault, ScriptedClient(responses), _config())

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
