from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from groundtruth.api.app import create_app
from groundtruth.api.services import Services
from groundtruth.auth import build_strategy
from groundtruth.llm.client import LLMResponse
from groundtruth.storage.job_store import JobStore
from groundtruth.storage.registry import VaultRegistry
from groundtruth.storage.source_index import SourceIndex
from groundtruth.web.views import build_web_router

pytestmark = pytest.mark.integration


def _git(cwd: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


class ScriptedClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)

    def complete(self, role, messages, **kw):  # type: ignore[no-untyped-def]
        return self._responses.pop(0)


@pytest.fixture
def client(tmp_path: Path):  # type: ignore[no-untyped-def]
    def _make(responses: list | None = None) -> TestClient:
        repo = tmp_path / "repo"
        (repo / "work" / "companies").mkdir(parents=True)
        _git(repo, "init", "-b", "main")
        (repo / "work" / "schema.md").write_text("# Schema\n\n## Folders\n- companies/\n")
        (repo / "work" / "companies" / "Acme.md").write_text(
            "---\ntitle: Acme\ntags: [company]\nsources: []\n"
            "created: 2026-01-01\nupdated: 2026-01-01\n---\n\nAcme was founded in 1996.\n"
        )
        _git(repo, "add", "-A")
        _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed")
        state = tmp_path / "state"
        reg = VaultRegistry(state)
        reg.register("work", repo)
        services = Services(
            state_dir=str(state),
            registry=reg,
            job_store=JobStore(state),
            source_index=SourceIndex(state),
            client_override=ScriptedClient(responses or []),
        )
        app = create_app(auth=build_strategy("none"), routers=[build_web_router(services)])
        return TestClient(app)

    return _make


class TestIngestView:
    def test_vault_selector_lists_registered_vaults(self, client) -> None:  # type: ignore[no-untyped-def]
        html = client().get("/ingest").text
        assert '<option value="work">work</option>' in html

    def test_submit_shows_a_job_id_and_polls(self, client) -> None:  # type: ignore[no-untyped-def]
        c = client([])
        html = c.post("/ui/ingest", data={"vault": "work", "text": "hello"}).text
        assert "data-job-id=" in html
        assert 'class="badge queued"' in html
        assert 'hx-get="/ui/jobs/' in html  # progress polling

    def test_failed_job_shows_stage_and_reason(self, client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        c = client([])
        (tmp_path / "repo" / "work" / "dirty.md").write_text("x\n")  # dirty tree -> job fails
        job_html = c.post("/ui/ingest", data={"vault": "work", "text": "hello"}).text
        job_id = job_html.split('data-job-id="')[1].split('"')[0]
        for _ in range(100):  # poll like htmx would, until terminal
            status = c.get(f"/ui/jobs/{job_id}").text
            if 'data-state="succeeded"' in status or 'data-state="failed"' in status:
                break
            time.sleep(0.05)
        assert 'data-state="failed"' in status
        assert "clean-tree" in status


class TestQueueView:
    def test_lists_a_submitted_job_with_a_state_badge(self, client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        c = client([])
        (tmp_path / "repo" / "work" / "dirty.md").write_text("x\n")  # force the job to fail
        c.post("/ui/ingest", data={"vault": "work", "text": "hello"})
        for _ in range(50):
            page = c.get("/queue").text
            if "badge failed" in page:
                break
            time.sleep(0.05)
        assert "Ingest queue" in page
        assert "badge failed" in page
        assert "1</b> failed" in page  # count strip
        assert 'hx-get="/ui/queue"' in page  # auto-refresh wired

    def test_fragment_renders_standalone(self, client) -> None:  # type: ignore[no-untyped-def]
        frag = client([]).get("/ui/queue").text
        assert "most recent" in frag
        assert "<html" not in frag  # a fragment, not a full page

    def test_empty_queue_has_a_friendly_message(self, client) -> None:  # type: ignore[no-untyped-def]
        assert "No ingest jobs yet" in client([]).get("/queue").text

    def test_row_opens_a_job_detail_overlay(self, client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        c = client([])
        (tmp_path / "repo" / "work" / "dirty.md").write_text("x\n")  # force fail
        body = c.post("/ui/ingest", data={"vault": "work", "text": "some text here"}).text
        job_id = body.split('data-job-id="')[1].split('"')[0]
        for _ in range(50):
            if "badge failed" in c.get("/queue").text:
                break
            time.sleep(0.05)

        page = c.get("/queue").text
        assert '<th class="hide-sm num">Text</th>' in page  # new column
        assert f'hx-get="/ui/jobs/{job_id}/detail"' in page  # row opens the overlay
        assert 'id="job-modal"' in page  # the dialog exists

        detail = c.get(f"/ui/jobs/{job_id}/detail").text
        assert "Text size" in detail
        assert "14 B" in detail  # len("some text here")
        assert "clean-tree" in detail  # failure stage in the overlay


class TestQueryView:
    def test_answer_renders_with_citation_links_into_browse(self, client) -> None:  # type: ignore[no-untyped-def]
        c = client([LLMResponse(role="answer", model="m", text="Founded 1996. [[companies/Acme]]")])
        html = c.post("/ui/query", data={"vault": "work", "question": "when?"}).text
        assert 'data-outcome="answer"' in html
        assert 'href="/browse/work/companies/Acme"' in html

    def test_refusal_renders_as_a_refusal_not_an_error(self, client) -> None:  # type: ignore[no-untyped-def]
        c = client([LLMResponse(role="answer", model="m", text="the vault does not contain this")])
        html = c.post("/ui/query", data={"vault": "work", "question": "revenue?"}).text
        assert "callout refusal" in html
        assert 'data-outcome="refused"' in html
        assert "callout error" not in html
        assert "not an error" in html  # explanatory text

    def test_both_refusal_reasons_have_their_own_text(self, client) -> None:  # type: ignore[no-untyped-def]
        no_ev = (
            client([LLMResponse(role="answer", model="m", text="nothing here")])
            .post("/ui/query", data={"vault": "work", "question": "q"})
            .text
        )
        assert "does not contain information" in no_ev

    def test_untrusted_answer_text_is_escaped(self, client) -> None:  # type: ignore[no-untyped-def]
        c = client(
            [
                LLMResponse(
                    role="answer", model="m", text="<script>alert(1)</script> [[companies/Acme]]"
                )
            ]
        )
        html = c.post("/ui/query", data={"vault": "work", "question": "x"}).text
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html


def test_no_business_logic_in_views() -> None:
    text = (Path(__file__).parents[2] / "src/groundtruth/web/views.py").read_text()
    for banned in ("GitRepo", "resolve_in_vault", "check_grounding", "IngestPipeline", "recover("):
        assert banned not in text
    assert "services." in text
