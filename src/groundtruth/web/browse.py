"""Web UI: read-only browse view (spec §10.3, §3.3).

Vault tree + note viewer. **Read-only** — editing happens in Obsidian. There is
no write endpoint and no edit/delete/create affordance anywhere in this module.
Adapter only: each handler is one or two :class:`Services` calls plus a render.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..api.services import Services
from .render import render_note_body

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _tree(paths: list[str]) -> dict[str, Any]:
    root: dict[str, Any] = {"dirs": {}, "notes": []}
    for path in sorted(paths):
        parts = path.split("/")
        node = root
        for part in parts[:-1]:
            node = node["dirs"].setdefault(part, {"dirs": {}, "notes": []})
        node["notes"].append({"name": parts[-1], "path": path})
    return root


def build_browse_router(services: Services) -> APIRouter:
    router = APIRouter(include_in_schema=False)

    @router.get("/browse", response_class=HTMLResponse)
    def browse_index(request: Request) -> Any:
        return _TEMPLATES.TemplateResponse(
            request,
            "browse.html",
            {"vaults": [v.name for v in services.list_vaults()], "vault": None},
        )

    @router.get("/browse/{vault}", response_class=HTMLResponse)
    def browse_vault(request: Request, vault: str) -> Any:
        paths = [n.path for n in services.list_notes(vault)]
        return _TEMPLATES.TemplateResponse(
            request,
            "browse.html",
            {
                "vaults": [v.name for v in services.list_vaults()],
                "vault": vault,
                "tree": _tree(paths),
                "note": None,
            },
        )

    @router.get("/browse/{vault}/{path:path}", response_class=HTMLResponse)
    def browse_note(request: Request, vault: str, path: str) -> Any:
        note = services.read_note(vault, path)  # containment-checked (#7) -> 400 on traversal
        all_paths = [n.path for n in services.list_notes(vault)]
        return _TEMPLATES.TemplateResponse(
            request,
            "browse.html",
            {
                "vaults": [v.name for v in services.list_vaults()],
                "vault": vault,
                "tree": _tree(all_paths),
                "note": {
                    "path": note.path,
                    "frontmatter": {
                        "title": note.frontmatter.title,
                        "tags": note.frontmatter.tags,
                        "created": note.frontmatter.created.isoformat(),
                        "updated": note.frontmatter.updated.isoformat(),
                        "sources": note.frontmatter.sources,
                    },
                    "html": render_note_body(note.body, vault=vault, existing_paths=all_paths),
                },
            },
        )

    return router


__all__ = ["build_browse_router"]
