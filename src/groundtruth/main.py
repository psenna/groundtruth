"""Process entrypoint: one ASGI app serving the API, MCP and web (spec §4.2, §10).

``build_app()`` wires everything from ``config.yaml``: the auth strategy (§4.5),
the vault registry (seeded from ``config.yaml`` ``vaults:``), the :class:`Services`
hub, every API/web router, and the MCP server mounted at the configured endpoint.
On startup it reconciles interrupted jobs and sweeps old records (#29).
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

from fastapi import FastAPI

from .api.app import create_app
from .api.ingest import build_ingest_router
from .api.query import build_query_router
from .api.services import Services
from .api.vaults import build_vaults_router
from .auth import build_strategy
from .config import load_global_config
from .jobs.recovery import recover_on_startup
from .mcp.server import mcp_asgi_app
from .storage.job_store import JobStore
from .storage.registry import RegistryError, VaultRegistry
from .storage.source_index import SourceIndex
from .web.browse import build_browse_router
from .web.views import build_web_router


def build_app(
    *,
    config_path: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> FastAPI:
    import os

    env: Mapping[str, str] = dict(os.environ) if environ is None else environ
    config = load_global_config(cli_config=config_path, environ=env)
    state_dir = config.state_dir

    registry = VaultRegistry(state_dir)
    for name, repo_root in config.vaults.items():
        with contextlib.suppress(RegistryError):  # already registered -> leave it
            registry.register(name, repo_root)

    job_store = JobStore(state_dir, retention_days=config.job_retention_days)
    source_index = SourceIndex(state_dir)
    services = Services(
        state_dir=state_dir,
        registry=registry,
        job_store=job_store,
        source_index=source_index,
        cli_config=Path(config_path) if config_path else None,
        environ=env,
        llm_logging=config.llm_logging,
        llm_timeout_s=config.llm_timeout_s,
    )

    auth = build_strategy(
        config.server.auth,
        {"bearer_token_env": config.server.bearer_token_env},
        env,
    )

    routers = [
        build_vaults_router(registry=registry, source_index=source_index),
        build_ingest_router(services),
        build_query_router(services),
        build_web_router(services),
        build_browse_router(services),
    ]

    mcp_app, mcp = mcp_asgi_app(services, auth)

    def _repo_root_of(name: str) -> Path:
        vault = registry.get(name)
        return vault.repo_root if vault is not None else Path("/nonexistent")

    def _resubmit(vault: str, job_id: str) -> None:
        services.queue.submit(vault, job_id)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        recover_on_startup(job_store, repo_root_of=_repo_root_of, resubmit=_resubmit)
        async with mcp.session_manager.run():
            yield

    app = create_app(auth=auth, routers=routers)
    app.router.lifespan_context = lifespan
    app.mount(config.server.mcp_endpoint, mcp_app)
    return app


def main() -> None:  # pragma: no cover - process entrypoint
    import uvicorn

    config = load_global_config()
    uvicorn.run(build_app(), host=config.server.bind, port=config.server.port)


if __name__ == "__main__":  # pragma: no cover
    main()
