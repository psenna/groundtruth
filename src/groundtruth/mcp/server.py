"""MCP server over streamable HTTP (spec §10.2, §4.5).

Served in-process with the API, at ``/mcp``, and using the **same** auth layer
(#30) — there is no second authentication path. Adapter only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..auth import AuthStrategy
from .tools import register_tools

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP
    from starlette.types import ASGIApp, Receive, Scope, Send

    from ..api.services import Services

MCP_MOUNT_PATH = "/mcp"


def build_mcp(services: Services) -> FastMCP:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    mcp = FastMCP(
        name="groundtruth",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",  # mounted at /mcp by the API; served at root standalone
        # DNS-rebinding protection is redundant here: the server is in-process
        # behind the API and its SharedAuthMiddleware.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    register_tools(mcp, services)
    return mcp


class SharedAuthMiddleware:
    """Rejects a request the API's auth layer would reject. No separate token logic."""

    def __init__(self, app: ASGIApp, *, auth: AuthStrategy) -> None:
        self._app = app
        self._auth = auth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            header = dict(scope.get("headers") or ()).get(b"authorization")
            credential = header.decode() if header else None
            if self._auth.authenticate(credential) is None:
                await _reject(send)
                return
        await self._app(scope, receive, send)


async def _reject(send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": b'{"detail":"authentication required"}'})


def mcp_asgi_app(services: Services, auth: AuthStrategy) -> tuple[ASGIApp, FastMCP]:
    """Return ``(asgi_app, mcp)``. Mount ``asgi_app`` at ``/mcp`` in the API app.

    The returned app is the FastMCP Starlette app with :class:`SharedAuthMiddleware`
    added, so its session-manager lifespan is preserved.
    """
    mcp = build_mcp(services)
    app = mcp.streamable_http_app()
    app.add_middleware(SharedAuthMiddleware, auth=auth)
    return app, mcp


__all__ = ["MCP_MOUNT_PATH", "SharedAuthMiddleware", "build_mcp", "mcp_asgi_app"]
