"""Query, notes and schema read endpoints (spec §10.1, §8, §9.1).

Adapter only. Query delegates to recovery (#24) + the unbypassable grounding
check (#25). There is **no write path** in this module (invariant 1).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .app import outcome_response
from .services import Services


class QueryRequest(BaseModel):
    vault: str
    question: str = Field(min_length=1)


def build_query_router(services: Services) -> APIRouter:
    router = APIRouter()

    @router.post("/query")
    def query(request: QueryRequest) -> Any:
        # Both an answer and a refusal are HTTP 200 (§8.4).
        return outcome_response(services.query(request.vault, request.question))

    @router.get("/notes")
    def list_notes(
        vault: str, tag: str | None = None, path: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            {"path": n.path, "title": n.frontmatter.title, "tags": n.frontmatter.tags}
            for n in services.list_notes(vault, tag=tag, path_prefix=path)
        ]

    @router.get("/notes/{vault}/{path:path}")
    def read_note(vault: str, path: str) -> dict[str, Any]:
        note = services.read_note(vault, path)
        return {
            "path": note.path,
            "frontmatter": {
                "title": note.frontmatter.title,
                "tags": note.frontmatter.tags,
                "sources": note.frontmatter.sources,
                "created": note.frontmatter.created.isoformat(),
                "updated": note.frontmatter.updated.isoformat(),
            },
            "body": note.body,
        }

    @router.get("/schema/{vault}")
    def read_schema(vault: str) -> dict[str, str]:
        return {"schema_md": services.read_schema(vault)}

    return router


__all__ = ["build_query_router"]
