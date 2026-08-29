"""The eight MCP tool adapters (spec §10.2).

Thin protocol adapters over :class:`Services` — the **same** engine calls the REST
API uses. Dedup, atomicity, budgets, the grounding check and the
``allow_schema_writes`` gate all live below this layer, so they apply identically
and are not re-checked here. A tool body should be one delegating call plus shape
translation — nothing else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..api.ingest import JobResponse
from ..recovery.format import to_payload

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from ..api.services import Services

#: The tools of §10.2, in table order.
TOOL_NAMES = (
    "groundtruth_query",
    "groundtruth_ingest",
    "job_status",
    "list_vaults",
    "list_notes",
    "read_note",
    "get_schema",
    "update_schema",
)

_WAIT_TIMEOUT = 120.0


def register_tools(mcp: FastMCP, services: Services) -> None:
    @mcp.tool()
    def groundtruth_query(vault: str, question: str) -> dict[str, Any]:
        """Answer a question from a vault's notes, or refuse. Same shape as the API."""
        return to_payload(services.query(vault, question))

    @mcp.tool()
    def groundtruth_ingest(
        vault: str, text: str, source: str = "mcp", wait: bool = False
    ) -> dict[str, Any]:
        """Ingest text into a vault. Returns a job id, or the result when wait=true."""
        result = services.submit_ingest(vault, text, source, wait=wait, timeout=_WAIT_TIMEOUT)
        if isinstance(result, str):
            return {"id": result, "state": "queued"}
        return JobResponse.of(result).model_dump()

    @mcp.tool()
    def job_status(job_id: str) -> dict[str, Any]:
        """Return a job record."""
        return JobResponse.of(services.get_job(job_id)).model_dump()

    @mcp.tool()
    def list_vaults() -> list[dict[str, str]]:
        """List registered vaults and their metadata."""
        return [{"name": v.name, "repo_root": str(v.repo_root)} for v in services.list_vaults()]

    @mcp.tool()
    def list_notes(
        vault: str, path: str | None = None, tag: str | None = None
    ) -> list[dict[str, Any]]:
        """List a vault's notes, optionally filtered by path prefix and/or tag."""
        return [
            {"path": n.path, "title": n.frontmatter.title, "tags": n.frontmatter.tags}
            for n in services.list_notes(vault, tag=tag, path_prefix=path)
        ]

    @mcp.tool()
    def read_note(vault: str, path: str) -> dict[str, Any]:
        """Return one note's frontmatter and body. Path is containment-checked."""
        note = services.read_note(vault, path)
        return {"path": note.path, "tags": note.frontmatter.tags, "body": note.body}

    @mcp.tool()
    def get_schema(vault: str) -> dict[str, str]:
        """Return a vault's schema.md."""
        return {"schema_md": services.read_schema(vault)}

    @mcp.tool()
    def update_schema(vault: str, markdown: str, rationale: str) -> dict[str, Any]:
        """Replace a vault's schema.md. Refused unless allow_schema_writes is enabled (§5.2)."""
        services.update_schema(vault, markdown, rationale)
        return {"ok": True}


__all__ = ["TOOL_NAMES", "register_tools"]
