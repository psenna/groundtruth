from __future__ import annotations

import contextlib
import subprocess
from pathlib import Path

import httpx
import pytest

from groundtruth.api.services import Services
from groundtruth.auth import build_strategy
from groundtruth.config import BUILTIN_DEFAULTS
from groundtruth.llm.client import LLMResponse
from groundtruth.mcp.server import mcp_asgi_app
from groundtruth.mcp.tools import TOOL_NAMES
from groundtruth.storage.job_store import JobStore
from groundtruth.storage.registry import VaultRegistry
from groundtruth.storage.source_index import SourceIndex

pytestmark = pytest.mark.integration


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class ScriptedClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)

    def complete(self, role, messages, **kw):  # type: ignore[no-untyped-def]
        return self._responses.pop(0)


@contextlib.asynccontextmanager
async def _session(app):  # type: ignore[no-untyped-def]
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    def factory(**kw: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", **kw
        )

    async with (
        app.router.lifespan_context(app),
        streamablehttp_client("http://127.0.0.1/", httpx_client_factory=factory) as (rd, wr, _),
        ClientSession(rd, wr) as session,
    ):
        await session.initialize()
        yield session


def _services(tmp_path: Path, *, allow_schema_writes: bool = False, responses: list | None = None):  # type: ignore[no-untyped-def]
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
    if allow_schema_writes:
        (repo / ".groundtruth.yaml").write_text("allow_schema_writes: true\n")
        _git(repo, "add", "-A")
        _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "enable")

    state = tmp_path / "state"
    reg = VaultRegistry(state)
    reg.register("work", repo)
    return Services(
        state_dir=str(state),
        registry=reg,
        job_store=JobStore(state),
        source_index=SourceIndex(state),
        client_override=ScriptedClient(responses or []),
    ), repo


async def _call(app, name: str, args: dict):  # type: ignore[no-untyped-def]
    async with _session(app) as session:
        return await session.call_tool(name, args)


class TestSurface:
    def test_default_allow_schema_writes_is_false(self) -> None:
        assert BUILTIN_DEFAULTS["allow_schema_writes"] is False

    @pytest.mark.anyio
    async def test_all_eight_tools_present(self, tmp_path: Path) -> None:
        services, _ = _services(tmp_path)
        app, _ = mcp_asgi_app(services, build_strategy("none"))
        async with _session(app) as session:
            names = {t.name for t in (await session.list_tools()).tools}
        assert (
            names
            == set(TOOL_NAMES)
            == {
                "groundtruth_query",
                "groundtruth_ingest",
                "job_status",
                "list_vaults",
                "list_notes",
                "read_note",
                "get_schema",
                "update_schema",
            }
        )


class TestQueryAndIngest:
    @pytest.mark.anyio
    async def test_query_returns_citations_in_the_api_shape(self, tmp_path: Path) -> None:
        services, _ = _services(
            tmp_path,
            responses=[LLMResponse(role="answer", model="m", text="1996 [[companies/Acme]]")],
        )
        app, _ = mcp_asgi_app(services, build_strategy("none"))
        result = await _call(app, "groundtruth_query", {"vault": "work", "question": "when?"})
        payload = result.structuredContent or {}
        assert payload["outcome"] == "answer"
        assert payload["citations"] == [{"vault": "work", "path": "companies/Acme"}]

    @pytest.mark.anyio
    async def test_query_refusal_same_shape_as_api(self, tmp_path: Path) -> None:
        services, _ = _services(
            tmp_path,
            responses=[LLMResponse(role="answer", model="m", text="[[companies/Ghost]]")],
        )
        app, _ = mcp_asgi_app(services, build_strategy("none"))
        result = await _call(app, "groundtruth_query", {"vault": "work", "question": "x"})
        assert (result.structuredContent or {})["outcome"] == "refused"

    @pytest.mark.anyio
    async def test_ingest_honours_dedup_and_wait(self, tmp_path: Path) -> None:
        from datetime import date

        from groundtruth.ingest.dedup import content_hash
        from groundtruth.models import SourceRecord

        text = "Some ingested text.\n"
        services, _ = _services(tmp_path)
        services.source_index.put(
            "work",
            SourceRecord(
                sha256=content_hash(text),
                job_id="01PRIOR",
                commit_sha="c0ffee",
                notes_touched=["companies/Acme.md"],
                ingested_at=date(2026, 1, 1),
            ),
        )
        app, _ = mcp_asgi_app(services, build_strategy("none"))
        result = await _call(
            app, "groundtruth_ingest", {"vault": "work", "text": text, "wait": True}
        )
        payload = result.structuredContent or {}
        assert payload["state"] == "succeeded"
        assert payload["deduplicated"] is True


class TestSchemaLock:
    @pytest.mark.anyio
    async def test_update_schema_refused_when_flag_is_false(self, tmp_path: Path) -> None:
        services, repo = _services(tmp_path, allow_schema_writes=False)
        app, _ = mcp_asgi_app(services, build_strategy("none"))
        result = await _call(
            app,
            "update_schema",
            {"vault": "work", "markdown": "# hacked\n", "rationale": "nope"},
        )
        assert result.isError
        assert "# Schema" in (repo / "work" / "schema.md").read_text()  # unchanged

    @pytest.mark.anyio
    async def test_update_schema_writes_content_when_enabled(self, tmp_path: Path) -> None:
        services, repo = _services(tmp_path, allow_schema_writes=True)
        app, _ = mcp_asgi_app(services, build_strategy("none"))
        new = "# Schema\n\n## Folders\n- companies/\n- projects/\n"
        result = await _call(
            app, "update_schema", {"vault": "work", "markdown": new, "rationale": "add projects"}
        )
        assert not result.isError
        assert (repo / "work" / "schema.md").read_text() == new

    def test_update_schema_is_the_only_writer_of_schema_md(self) -> None:
        src = Path(__file__).parents[2] / "src" / "groundtruth"
        writers: set[str] = set()
        for py in src.rglob("*.py"):
            lines = py.read_text().splitlines()
            for i in range(len(lines)):
                window = " ".join(lines[max(0, i - 1) : i + 2])
                if ".write_text(" in window and (
                    "schema.md" in window or "_SCHEMA_FILENAME" in window
                ):
                    writers.add(py.name)
        # schema.py defines write_schema; scaffold.py writes the §13.1 starter once
        assert writers <= {"schema.py", "scaffold.py"}
        # and no ingest module reaches write_schema
        for py in (src / "ingest").glob("*.py"):
            if py.name != "schema.py":
                assert "write_schema" not in py.read_text()


class TestContainmentAndInspection:
    @pytest.mark.anyio
    async def test_read_note_traversal_is_refused(self, tmp_path: Path) -> None:
        services, _ = _services(tmp_path)
        app, _ = mcp_asgi_app(services, build_strategy("none"))
        result = await _call(app, "read_note", {"vault": "work", "path": "../../etc/passwd"})
        assert result.isError

    def test_tool_bodies_contain_no_business_logic(self) -> None:
        text = (Path(__file__).parents[2] / "src/groundtruth/mcp/tools.py").read_text()
        for banned in (
            "GitRepo",
            "resolve_in_vault",
            "check_grounding",
            "validate(",
            "write_schema",
        ):
            assert banned not in text  # all of it lives below this layer
        assert "services." in text  # every tool delegates
