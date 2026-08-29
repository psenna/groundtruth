from __future__ import annotations

import contextlib
from pathlib import Path

import httpx
import pytest

from groundtruth.api.services import Services
from groundtruth.auth import build_strategy
from groundtruth.mcp.server import build_mcp, mcp_asgi_app
from groundtruth.mcp.tools import TOOL_NAMES
from groundtruth.storage.job_store import JobStore
from groundtruth.storage.registry import VaultRegistry
from groundtruth.storage.source_index import SourceIndex

pytestmark = pytest.mark.integration


@pytest.fixture
def services(tmp_path: Path) -> Services:
    vdir = tmp_path / "repo" / "work"
    vdir.mkdir(parents=True)
    (vdir / "schema.md").write_text("# Schema\n\n## Folders\n- companies/\n")
    state = tmp_path / "state"
    reg = VaultRegistry(state)
    reg.register("work", tmp_path / "repo")
    return Services(
        state_dir=str(state),
        registry=reg,
        job_store=JobStore(state),
        source_index=SourceIndex(state),
    )


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


class TestServer:
    @pytest.mark.anyio
    async def test_initialize_handshake_and_tool_list(self, services: Services) -> None:
        app, _ = mcp_asgi_app(services, build_strategy("none"))
        async with _session(app) as session:
            tools = await session.list_tools()
        assert {t.name for t in tools.tools} == set(TOOL_NAMES)
        assert len(TOOL_NAMES) == 8

    @pytest.mark.anyio
    async def test_every_vault_tool_takes_a_vault_parameter(self, services: Services) -> None:
        app, _ = mcp_asgi_app(services, build_strategy("none"))
        async with _session(app) as session:
            tools = {t.name: t for t in (await session.list_tools()).tools}
        for name, tool in tools.items():
            if name in ("job_status", "list_vaults"):
                continue
            assert "vault" in set(tool.inputSchema.get("properties", {}))

    @pytest.mark.anyio
    async def test_unregistered_vault_is_a_clean_tool_error(self, services: Services) -> None:
        app, _ = mcp_asgi_app(services, build_strategy("none"))
        async with _session(app) as session:
            result = await session.call_tool("get_schema", {"vault": "ghost"})
        assert result.isError  # not a crash / disconnect

    @pytest.mark.anyio
    async def test_bearer_rejects_unauthenticated(self, services: Services) -> None:
        auth = build_strategy("bearer", {"bearer_token_env": "T"}, {"T": "sekret"})
        app, _ = mcp_asgi_app(services, auth)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            resp = await client.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
            assert resp.status_code == 401


def test_auth_is_the_shared_layer_not_reimplemented() -> None:
    text = (Path(__file__).parents[2] / "src/groundtruth/mcp/server.py").read_text()
    assert "from ..auth import" in text
    assert "self._auth.authenticate(" in text  # delegates to the strategy
    assert "hmac" not in text and "compare_digest" not in text  # no comparison of its own


def test_build_mcp_registers_all_eight(services: Services) -> None:
    mcp = build_mcp(services)
    import anyio

    names = {t.name for t in anyio.run(mcp.list_tools)}
    assert names == set(TOOL_NAMES)
