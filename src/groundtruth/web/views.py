"""Web UI: query, ingest and job-queue views (spec §10.3). htmx over FastAPI,
no build step.

Adapter only — every handler is a form/param parse plus one or two
:class:`Services` calls plus a template render. A refusal renders **as a
refusal**, visually distinct from an error and from an empty result. The
``_ago`` / ``_duration`` / ``_job_row`` helpers are presentation formatting, not
business logic.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..api.ingest import JobResponse
from ..api.services import Services
from ..models import JobRecord, JobState, Refusal
from ..recovery.format import render_answer, render_refusal

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_WIKILINK = re.compile(r"\[\[([^\[\]|]+?)\]\]")


def _ago(when: datetime | None, now: datetime) -> str:
    if when is None:
        return "—"
    secs = max(0, int((now - when).total_seconds()))
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if secs >= size:
            return f"{secs // size}{unit} ago"
    return f"{secs}s ago" if secs else "just now"


def _duration(rec: JobRecord, now: datetime) -> str:
    total = sum(rec.stage_timings.values())
    if not total and rec.state is JobState.RUNNING and rec.created_at is not None:
        total = (now - rec.created_at).total_seconds()
    if not total:
        return "—"
    return f"{total:.1f}s" if total < 60 else f"{total / 60:.1f}m"


def _job_row(rec: JobRecord, now: datetime) -> dict[str, Any]:
    return {
        "id": rec.id,
        "vault": rec.vault,
        "state": rec.state.value,
        "created": _ago(rec.created_at, now),
        "duration": _duration(rec, now),
        "notes": len(rec.notes_created) + len(rec.notes_updated),
        "created_n": len(rec.notes_created),
        "updated_n": len(rec.notes_updated),
        "deduplicated": rec.dedup_of is not None,
        "failure_stage": rec.failure_stage,
        "error": rec.error,
        "attempts": rec.attempts,
    }


def _queue_context(services: Services, limit: int) -> dict[str, Any]:
    now = datetime.now(UTC)
    recs = services.list_recent_jobs(limit)
    rows = [_job_row(r, now) for r in recs]
    counts = {s.value: sum(1 for r in recs if r.state is s) for s in JobState}
    return {"rows": rows, "counts": counts, "total": len(recs)}


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
    # The htmx form/poll endpoints live under /ui/ so they never collide with the
    # JSON API's POST /query, POST /ingest and GET /jobs/{id} — both routers are
    # mounted on the same app, and a form body posted to the JSON route 422s.
    router = APIRouter(include_in_schema=False)

    def _vaults() -> list[str]:
        return [v.name for v in services.list_vaults()]

    @router.get("/", response_class=HTMLResponse)
    def query_view(request: Request) -> Any:
        return _TEMPLATES.TemplateResponse(
            request, "query.html", {"vaults": _vaults(), "nav": "query"}
        )

    @router.get("/ingest", response_class=HTMLResponse)
    def ingest_view(request: Request) -> Any:
        return _TEMPLATES.TemplateResponse(
            request, "ingest.html", {"vaults": _vaults(), "nav": "ingest"}
        )

    @router.post("/ui/ingest", response_class=HTMLResponse)
    def submit_ingest(request: Request, vault: str = Form(...), text: str = Form(...)) -> Any:
        result = services.submit_ingest(vault, text, "web", wait=False, timeout=0.0)
        job_id = result if isinstance(result, str) else result.id
        job = JobResponse.of(services.get_job(job_id)).model_dump()
        return _TEMPLATES.TemplateResponse(request, "_job.html", {"job": job})

    @router.get("/ui/jobs/{job_id}", response_class=HTMLResponse)
    def job_status(request: Request, job_id: str) -> Any:
        job = JobResponse.of(services.get_job(job_id)).model_dump()
        return _TEMPLATES.TemplateResponse(request, "_job.html", {"job": job})

    @router.get("/queue", response_class=HTMLResponse)
    def queue_view(request: Request, limit: int = 100) -> Any:
        ctx: dict[str, Any] = {"nav": "queue"}
        ctx.update(_queue_context(services, limit))
        return _TEMPLATES.TemplateResponse(request, "queue.html", ctx)

    @router.get("/ui/queue", response_class=HTMLResponse)
    def queue_fragment(request: Request, limit: int = 100) -> Any:
        return _TEMPLATES.TemplateResponse(
            request, "_queue_table.html", _queue_context(services, limit)
        )

    @router.post("/ui/query", response_class=HTMLResponse)
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
