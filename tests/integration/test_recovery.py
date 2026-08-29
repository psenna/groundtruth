from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from groundtruth.models import Vault
from groundtruth.recovery.agent import recover
from groundtruth.retrieval.agent import AgentStatus
from groundtruth.retrieval.tools import ReadOnlyTools

pytestmark = pytest.mark.integration


def _vault_digest(vault_dir: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(vault_dir.rglob("*")):
        if path.is_file():
            h.update(path.relative_to(vault_dir).as_posix().encode())
            h.update(path.read_bytes())
    return h.hexdigest()


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    vdir = tmp_path / "repo" / "work"
    (vdir / "companies").mkdir(parents=True)
    (vdir / "schema.md").write_text("# Schema\n\n## Folders\n- companies/\n")
    (vdir / "companies" / "Acme.md").write_text(
        "---\ntitle: Acme\ntags: [company]\nsources: []\n"
        "created: 2026-01-01\nupdated: 2026-01-01\n---\n\nAcme was founded in 1996.\n"
    )
    return Vault(name="work", repo_root=tmp_path / "repo")


class ScriptedClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict]] = []

    def complete(self, role: str, messages: object, **kw: object) -> object:
        self.calls.append(list(messages))  # type: ignore[arg-type]
        return self._responses.pop(0)


def _answer(text: str):
    from groundtruth.llm.client import LLMResponse

    return LLMResponse(role="answer", model="m", text=text)


def _grep(pattern: str):
    from groundtruth.llm.client import LLMResponse, ToolCall

    return LLMResponse(
        role="answer",
        model="m",
        text=None,
        tool_calls=[ToolCall(id="c", name="grep", arguments={"pattern": pattern})],
    )


def _assert_vault_unchanged(vault: Vault, before: str) -> None:
    assert _vault_digest(vault.vault_dir) == before


class TestRecovery:
    def test_schema_is_in_the_first_prompt_before_any_tool_call(self, vault: Vault) -> None:
        before = _vault_digest(vault.vault_dir)
        client = ScriptedClient(
            [_grep("Acme"), _answer("Acme was founded in 1996. [[companies/Acme]]")]
        )

        outcome = recover(vault, "When was Acme founded?", client)

        first_prompt = client.calls[0][0]["content"]
        assert "## Folders" in first_prompt  # schema.md content, sent before the loop runs
        assert outcome.transcript[0].name == "grep"  # the first tool call comes after
        _assert_vault_unchanged(vault, before)

    def test_only_read_only_tools_are_exposed(self, vault: Vault) -> None:
        client = ScriptedClient([_answer("no [[companies/Acme]]")])
        recover(vault, "q", client)
        names = {s["function"]["name"] for s in ReadOnlyTools.TOOL_SCHEMAS}
        assert names == {"ls", "grep", "read"}

    def test_module_cannot_construct_a_write_tool(self) -> None:
        source = (Path(__file__).parents[2] / "src/groundtruth/recovery/agent.py").read_text()
        assert "write_tools" not in source
        assert "WriteTools" not in source

    def test_vault_is_byte_identical_after_a_query(self, vault: Vault) -> None:
        before = _vault_digest(vault.vault_dir)
        client = ScriptedClient(
            [_grep("founded"), _grep("Acme"), _answer("Founded 1996. [[companies/Acme]]")]
        )
        recover(vault, "When?", client)
        _assert_vault_unchanged(vault, before)

    def test_budget_exhaustion_is_an_outcome_not_an_exception(self, vault: Vault) -> None:
        from groundtruth.config import Limits

        limits = Limits(
            max_notes_per_ingest=10,
            max_note_bytes=65536,
            max_tool_calls=1,
            max_wall_clock_s=60,
            grep_max_matches=50,
            grep_max_bytes=65536,
            read_max_bytes=32768,
            vocab_max_bytes=4096,
        )
        client = ScriptedClient([_grep("x")] * 3)
        outcome = recover(vault, "q", client, limits=limits)
        assert outcome.status is AgentStatus.EXHAUSTED

    def test_explicit_vault_no_single_vault_assumption(self, tmp_path: Path) -> None:
        def _make(name: str) -> Vault:
            vdir = tmp_path / name / name
            vdir.mkdir(parents=True)
            (vdir / "schema.md").write_text("# Schema\n\n## Folders\n- x/\n")
            return Vault(name=name, repo_root=tmp_path / name)

        a, b = _make("alpha"), _make("beta")
        recover(a, "q", ScriptedClient([_answer("a [[x/n]]")]))
        recover(b, "q", ScriptedClient([_answer("b [[x/n]]")]))  # no shared state, no error

    def test_empty_vault_query_terminates_promptly(self, tmp_path: Path) -> None:
        vdir = tmp_path / "e" / "e"
        vdir.mkdir(parents=True)
        (vdir / "schema.md").write_text("# Schema\n\n## Folders\n- x/\n")
        client = ScriptedClient([_answer("The vault does not contain this.")])
        outcome = recover(Vault(name="e", repo_root=tmp_path / "e"), "q", client)
        assert outcome.status is AgentStatus.COMPLETED
        assert len(client.calls) == 1
