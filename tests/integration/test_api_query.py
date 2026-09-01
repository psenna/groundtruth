from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from groundtruth.api.app import create_app
from groundtruth.api.query import build_query_router
from groundtruth.api.services import Services
from groundtruth.auth import build_strategy
from groundtruth.llm.client import LLMResponse, TokenUsage, ToolCall
from groundtruth.models import Vault
from groundtruth.storage.job_store import JobStore
from groundtruth.storage.registry import VaultRegistry
from groundtruth.storage.source_index import SourceIndex

pytestmark = pytest.mark.integration


class ScriptedClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)

    def complete(self, role, messages, **kw):  # type: ignore[no-untyped-def]
        return self._responses.pop(0)


def _grep(pattern: str) -> LLMResponse:
    return LLMResponse(
        role="answer",
        model="m",
        text=None,
        tool_calls=[ToolCall(id="c", name="grep", arguments={"pattern": pattern})],
    )


def _digest(vault_dir: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(vault_dir.rglob("*")):
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


@pytest.fixture
def make_client(tmp_path: Path):  # type: ignore[no-untyped-def]
    def _make(responses: list) -> tuple[TestClient, Vault]:
        vdir = tmp_path / "repo" / "work"
        (vdir / "companies").mkdir(parents=True)
        (vdir / "schema.md").write_text("# Schema\n\n## Folders\n- companies/\n")
        (vdir / "companies" / "Acme.md").write_text(
            "---\ntitle: Acme\ntags: [company, vendor]\nsources: []\n"
            "created: 2026-01-01\nupdated: 2026-01-01\n---\n\nAcme was founded in 1996.\n"
        )
        state = tmp_path / "state"
        registry = VaultRegistry(state)
        registry.register("work", tmp_path / "repo")
        services = Services(
            state_dir=str(state),
            registry=registry,
            job_store=JobStore(state),
            source_index=SourceIndex(state),
            client_override=ScriptedClient(responses),
        )
        app = create_app(auth=build_strategy("none"), routers=[build_query_router(services)])
        return TestClient(app), Vault(name="work", repo_root=tmp_path / "repo")

    return _make


class TestQuery:
    def test_answerable_question_returns_answer_with_citations(self, make_client) -> None:  # type: ignore[no-untyped-def]
        client, _ = make_client(
            [
                _grep("founded"),
                LLMResponse(role="answer", model="m", text="1996. [[companies/Acme]]"),
            ]
        )
        body = client.post("/query", json={"vault": "work", "question": "When?"}).json()
        assert body["outcome"] == "answer"
        assert body["citations"] == [{"vault": "work", "path": "companies/Acme"}]

    def test_unanswerable_question_returns_200_refusal(self, make_client) -> None:  # type: ignore[no-untyped-def]
        client, _ = make_client(
            [LLMResponse(role="answer", model="m", text="The vault does not contain this.")]
        )
        resp = client.post("/query", json={"vault": "work", "question": "revenue?"})
        assert resp.status_code == 200
        assert resp.json()["outcome"] == "refused"
        assert resp.json()["reason"] == "no_evidence"

    def test_fabricated_citation_is_downgraded_to_refusal(self, make_client) -> None:  # type: ignore[no-untyped-def]
        client, _ = make_client(
            [LLMResponse(role="answer", model="m", text="Yes. [[companies/Fictional]]")]
        )
        body = client.post("/query", json={"vault": "work", "question": "x"}).json()
        assert body["outcome"] == "refused"  # grounding check is unbypassable

    def test_budget_exhaustion_returns_budget_exhausted_refusal(self, tmp_path: Path) -> None:
        vdir = tmp_path / "repo" / "work"
        vdir.mkdir(parents=True)
        (vdir / "schema.md").write_text("# Schema\n\n## Folders\n- x/\n")
        state = tmp_path / "state"
        reg = VaultRegistry(state)
        reg.register("work", tmp_path / "repo")
        # per-vault limit: 1 tool call, then exhausted
        (tmp_path / "repo" / ".groundtruth.yaml").write_text("limits:\n  max_tool_calls: 1\n")
        services = Services(
            state_dir=str(state),
            registry=reg,
            job_store=JobStore(state),
            source_index=SourceIndex(state),
            client_override=ScriptedClient([_grep("x")] * 3),
        )
        client = TestClient(
            create_app(auth=build_strategy("none"), routers=[build_query_router(services)])
        )
        body = client.post("/query", json={"vault": "work", "question": "x"}).json()
        assert body["outcome"] == "refused"
        assert body["reason"] == "budget_exhausted"
        assert body["message"].startswith("Could not establish ground truth")

    def test_answer_payload_reports_token_usage(self, make_client) -> None:  # type: ignore[no-untyped-def]
        client, _ = make_client(
            [
                LLMResponse(
                    role="answer",
                    model="m",
                    text=None,
                    tool_calls=[ToolCall(id="c", name="grep", arguments={"pattern": "founded"})],
                    usage=TokenUsage(30, 10, 40),
                ),
                LLMResponse(
                    role="answer",
                    model="m",
                    text="1996. [[companies/Acme]]",
                    usage=TokenUsage(50, 12, 62),
                ),
            ]
        )
        body = client.post("/query", json={"vault": "work", "question": "When?"}).json()
        assert body["outcome"] == "answer"
        assert body["token_usage"] == {
            "answer": {"prompt_tokens": 80, "completion_tokens": 22, "total_tokens": 102}
        }

    def test_budget_exhausted_refusal_reports_token_usage(self, tmp_path: Path) -> None:
        vdir = tmp_path / "repo" / "work"
        vdir.mkdir(parents=True)
        (vdir / "schema.md").write_text("# Schema\n\n## Folders\n- x/\n")
        state = tmp_path / "state"
        reg = VaultRegistry(state)
        reg.register("work", tmp_path / "repo")
        (tmp_path / "repo" / ".groundtruth.yaml").write_text("limits:\n  max_tool_calls: 1\n")
        services = Services(
            state_dir=str(state),
            registry=reg,
            job_store=JobStore(state),
            source_index=SourceIndex(state),
            client_override=ScriptedClient(
                [
                    LLMResponse(
                        role="answer",
                        model="m",
                        text=None,
                        tool_calls=[ToolCall(id="c", name="grep", arguments={"pattern": "x"})],
                        usage=TokenUsage(9, 1, 10),
                    )
                ]
                * 3
            ),
        )
        client = TestClient(
            create_app(auth=build_strategy("none"), routers=[build_query_router(services)])
        )
        body = client.post("/query", json={"vault": "work", "question": "x"}).json()
        assert body["outcome"] == "refused"
        assert body["token_usage"]["answer"]["total_tokens"] == 10

    def test_query_never_modifies_the_vault(self, make_client) -> None:  # type: ignore[no-untyped-def]
        client, vault = make_client(
            [_grep("x"), LLMResponse(role="answer", model="m", text="1996 [[companies/Acme]]")]
        )
        before = _digest(vault.vault_dir)
        client.post("/query", json={"vault": "work", "question": "x"})
        assert _digest(vault.vault_dir) == before

    def test_unregistered_vault_is_422(self, make_client) -> None:  # type: ignore[no-untyped-def]
        client, _ = make_client([])
        assert client.post("/query", json={"vault": "ghost", "question": "x"}).status_code == 422


class TestNotesAndSchema:
    def test_list_notes_filters_by_tag_and_path(self, make_client) -> None:  # type: ignore[no-untyped-def]
        client, _ = make_client([])
        assert [n["path"] for n in client.get("/notes?vault=work&tag=vendor").json()] == [
            "companies/Acme.md"
        ]
        assert client.get("/notes?vault=work&tag=missing").json() == []
        assert [n["path"] for n in client.get("/notes?vault=work&path=companies/").json()] == [
            "companies/Acme.md"
        ]

    def test_read_note_returns_content(self, make_client) -> None:  # type: ignore[no-untyped-def]
        client, _ = make_client([])
        body = client.get("/notes/work/companies/Acme.md").json()
        assert "founded in 1996" in body["body"]
        assert body["frontmatter"]["tags"] == ["company", "vendor"]

    def test_traversal_is_refused(self, make_client) -> None:  # type: ignore[no-untyped-def]
        client, _ = make_client([])
        assert client.get("/notes/work/../../etc/passwd").status_code in (400, 404)

    def test_get_schema(self, make_client) -> None:  # type: ignore[no-untyped-def]
        client, _ = make_client([])
        assert "## Folders" in client.get("/schema/work").json()["schema_md"]
