"""Web UI: ingest and query views (spec §10.3). htmx over FastAPI, no build step.

Adapter only — every handler is a form parse plus one :class:`Services` call plus
a template render. A refusal renders **as a refusal**, visually distinct from an
error and from an empty result.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..api.ingest import JobResponse
from ..api.services import Services
from ..models import Refusal
from ..recovery.format import render_answer, render_refusal

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_WIKILINK = re.compile(r"\[\[([^\[\]|]+?)\]\]")


def _render_answer_html(vault: str, text: str) -> str:
    """Escape the answer prose and turn ``[[path]]`` into Browse links."""
    parts: list[str] = []
    last = 0
    for match in _WIKILINK.finditer(text):
        parts.append(html.escape(text[last : match.start()]))
        path = match.group(1).strip()
        parts.append(
            f'<a href="/browse/{html.escape(vault)}/{html.escape(path)}">'
            f"[[{html.escape(path)}]]</a>"
        )
        last = match.end()
    parts.append(html.escape(text[last:]))
    return "".join(parts).replace("\n", "<br>")


def build_web_router(services: Services) -> APIRouter:
    router = APIRouter(include_in_schema=False)

    def _vaults() -> list[str]:
        return [v.name for v in services.list_vaults()]

    @router.get("/", response_class=HTMLResponse)
    def query_view(request: Request) -> Any:
        return _TEMPLATES.TemplateResponse(request, "query.html", {"vaults": _vaults()})

    @router.get("/ingest", response_class=HTMLResponse)
    def ingest_view(request: Request) -> Any:
        return _TEMPLATES.TemplateResponse(request, "ingest.html", {"vaults": _vaults()})

    @router.post("/ingest", response_class=HTMLResponse)
    def submit_ingest(request: Request, vault: str = Form(...), text: str = Form(...)) -> Any:
        result = services.submit_ingest(vault, text, "web", wait=False, timeout=0.0)
        job_id = result if isinstance(result, str) else result.id
        job = JobResponse.of(services.get_job(job_id)).model_dump()
        return _TEMPLATES.TemplateResponse(request, "_job.html", {"job": job})

    @router.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_status(request: Request, job_id: str) -> Any:
        job = JobResponse.of(services.get_job(job_id)).model_dump()
        return _TEMPLATES.TemplateResponse(request, "_job.html", {"job": job})

    @router.post("/query", response_class=HTMLResponse)
    def submit_query(request: Request, vault: str = Form(...), question: str = Form(...)) -> Any:
        result = services.query(vault, question)
        context: dict[str, Any]
        if isinstance(result, Refusal):
            context = {
                "outcome": "refused",
                "reason": result.reason,
                "message": render_refusal(result),
            }
        else:
            context = {
                "outcome": "answer",
                "rendered_answer": _render_answer_html(vault, render_answer(result)),
                "citations": [{"vault": c.vault, "path": c.path} for c in result.citations],
            }
        return _TEMPLATES.TemplateResponse(request, "_answer.html", context)

    return router


__all__ = ["build_web_router"]
