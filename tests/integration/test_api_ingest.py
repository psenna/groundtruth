from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from groundtruth.api.app import create_app
from groundtruth.api.ingest import build_ingest_router
from groundtruth.api.services import Services
from groundtruth.auth import build_strategy
from groundtruth.ingest.dedup import content_hash
from groundtruth.llm.client import LLMResponse, TokenUsage, ToolCall
from groundtruth.models import SourceRecord
from groundtruth.storage.job_store import JobStore
from groundtruth.storage.registry import VaultRegistry
from groundtruth.storage.source_index import SourceIndex

pytestmark = pytest.mark.integration

TEXT = "Acme Corp was founded in 1996. It is a vendor.\n"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class ScriptedClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)

    def complete(self, role, messages, **kw):  # type: ignore[no-untyped-def]
        return self._responses.pop(0)


def _happy() -> list[LLMResponse]:
    return [
        LLMResponse(role="a", model="m", text="none"),
        LLMResponse(role="a", model="m", text="- Acme founded 1996."),
        LLMResponse(role="a", model="m", text="company\nvendor"),
        LLMResponse(
            role="organize",
            model="m",
            text=None,
            tool_calls=[
                ToolCall(
                    id="c",
                    name="create_note",
                    arguments={"folder": "companies", "title": "Acme", "body": "Founded 1996."},
                )
            ],
            usage=TokenUsage(1, 1, 2),
        ),
        LLMResponse(role="organize", model="m", text="done"),
    ]


@pytest.fixture
def env(tmp_path: Path) -> tuple[TestClient, Services, Path]:
    repo = tmp_path / "repo"
    (repo / "work").mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "work" / "schema.md").write_text("# Schema\n\n## Folders\n- companies/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "seed")

    state = tmp_path / "state"
    registry = VaultRegistry(state)
    registry.register("work", repo)
    services = Services(
        state_dir=str(state),
        registry=registry,
        job_store=JobStore(state),
        source_index=SourceIndex(state),
        client_override=ScriptedClient(_happy()),
    )
    client = TestClient(
        create_app(auth=build_strategy("none"), routers=[build_ingest_router(services)])
    )
    return client, services, repo


class TestIngest:
    def test_returns_job_id_immediately(self, env: tuple[TestClient, Services, Path]) -> None:
        client, _, _ = env
        body = client.post("/ingest", json={"vault": "work", "text": TEXT}).json()
        assert body["id"] and body["state"] == "queued"

    def test_wait_true_returns_completed_result(
        self, env: tuple[TestClient, Services, Path]
    ) -> None:
        client, _, _ = env
        body = client.post("/ingest?wait=true", json={"vault": "work", "text": TEXT}).json()
        assert body["state"] == "succeeded"
        assert body["notes_created"] == ["companies/Acme.md"]
        assert body["commit_sha"]

    def test_wait_true_on_failure_returns_the_failure(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        (repo / "work").mkdir(parents=True)
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "work" / "schema.md").write_text("# Schema\n\n## Folders\n- companies/\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "seed")
        (repo / "work" / "dirty.md").write_text("unsaved\n")  # dirty tree

        state = tmp_path / "state"
        reg = VaultRegistry(state)
        reg.register("work", repo)
        services = Services(
            state_dir=str(state),
            registry=reg,
            job_store=JobStore(state),
            source_index=SourceIndex(state),
            client_override=ScriptedClient([]),
        )
        client = TestClient(
            create_app(auth=build_strategy("none"), routers=[build_ingest_router(services)])
        )
        body = client.post("/ingest?wait=true", json={"vault": "work", "text": TEXT}).json()
        assert body["state"] == "failed"
        assert body["failure_stage"] == "clean-tree"

    def test_dedup_hit_is_flagged(self, env: tuple[TestClient, Services, Path]) -> None:
        client, services, _ = env
        services.source_index.put(
            "work",
            SourceRecord(
                sha256=content_hash(TEXT),
                job_id="01PRIOR",
                commit_sha="c0ffee",
                notes_touched=["companies/Acme.md"],
                ingested_at=__import__("datetime").date(2026, 1, 1),
            ),
        )
        body = client.post("/ingest?wait=true", json={"vault": "work", "text": TEXT}).json()
        assert body["state"] == "succeeded"
        assert body["deduplicated"] is True

    def test_unregistered_vault_is_422(self, env: tuple[TestClient, Services, Path]) -> None:
        client, _, _ = env
        resp = client.post("/ingest", json={"vault": "ghost", "text": TEXT})
        assert resp.status_code == 422

    def test_empty_text_is_422(self, env: tuple[TestClient, Services, Path]) -> None:
        client, _, _ = env
        assert client.post("/ingest", json={"vault": "work", "text": ""}).status_code == 422


class TestJobs:
    def test_get_job_returns_the_record(self, env: tuple[TestClient, Services, Path]) -> None:
        client, _, _ = env
        job_id = client.post("/ingest?wait=true", json={"vault": "work", "text": TEXT}).json()["id"]
        got = client.get(f"/jobs/{job_id}").json()
        assert got["id"] == job_id
        assert got["state"] == "succeeded"

    def test_unknown_job_is_404(self, env: tuple[TestClient, Services, Path]) -> None:
        client, _, _ = env
        assert client.get("/jobs/nope").status_code == 404

    def test_list_jobs_returns_recent_first(self, env: tuple[TestClient, Services, Path]) -> None:
        client, _, _ = env
        client.post("/ingest?wait=true", json={"vault": "work", "text": TEXT})
        client.post("/ingest?wait=true", json={"vault": "work", "text": TEXT + " more"})
        jobs = client.get("/jobs").json()
        assert len(jobs) == 2
        assert jobs[0]["updated_at"] >= jobs[1]["updated_at"]
        assert client.get("/jobs?limit=1").json() == [jobs[0]]

    def test_job_records_cannot_hold_a_secret(self, env: tuple[TestClient, Services, Path]) -> None:
        # The job store refuses a secret-shaped record outright (invariant 6);
        # the response layer also redacts defensively.
        from groundtruth.models import JobRecord, JobState
        from groundtruth.storage.job_store import JobStoreError

        _, services, _ = env
        services.job_store.create(JobRecord(id="leaky", vault="work"))
        running = services.job_store.update(
            services.job_store.load("leaky").transitioned_to(JobState.RUNNING)
        )
        with pytest.raises(JobStoreError):
            services.job_store.update(
                running.model_copy(
                    update={"error": "boom sk-ABCDEF0123456789ABCDEF"}
                ).transitioned_to(JobState.FAILED)
            )
