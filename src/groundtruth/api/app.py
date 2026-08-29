"""FastAPI application shell (spec §8.4, §10.1).

The load-bearing decision: **a refusal is a successful response** — HTTP 200 with
a structured outcome, not a 404 and not an error (§8.4). Adapter only: no
business logic lives here (the CLAUDE.md layer rule).

Error responses are scrubbed — no secret, token, or absolute host path ever
reaches the client.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter

from ..auth import ANONYMOUS, AuthStrategy, Principal
from ..errors import GroundtruthError, TransientError
from ..models import AnswerResult, Refusal
from ..recovery.format import to_payload

_ABS_PATH = re.compile(r"(?:/[\w.\-]+){2,}")


def _safe_detail(message: str) -> str:
    from ..redaction import redact

    return _ABS_PATH.sub("<path>", redact(message))


def outcome_response(result: AnswerResult | Refusal) -> JSONResponse:
    """Both an answer and a refusal are HTTP 200 with the shared payload shape (#26)."""
    return JSONResponse(status_code=200, content=to_payload(result))


def principal_dependency(auth: AuthStrategy) -> Callable[[Request], Principal]:
    def _resolve(request: Request) -> Principal:
        principal = auth.authenticate(request.headers.get("Authorization"))
        if principal is None:
            raise _ApiError(401, "authentication required")
        return principal

    return _resolve


class _ApiError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def create_app(
    *,
    auth: AuthStrategy,
    routers: Sequence[APIRouter] = (),
) -> FastAPI:
    app = FastAPI(title="groundtruth")
    require_principal = principal_dependency(auth)

    @app.get("/health")
    def health() -> dict[str, str]:  # no auth
        return {"status": "ok"}

    @app.get("/whoami")
    def whoami(
        principal: Principal = Depends(require_principal),  # noqa: B008 - FastAPI DI pattern
    ) -> dict[str, object]:
        return {"name": principal.name, "anonymous": principal.anonymous}

    for router in routers:
        app.include_router(router, dependencies=[Depends(require_principal)])

    @app.exception_handler(_ApiError)
    def _handle_api_error(_request: Request, exc: _ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})

    @app.exception_handler(TransientError)
    def _handle_transient(_request: Request, exc: TransientError) -> JSONResponse:
        return JSONResponse(
            status_code=503, content={"detail": _safe_detail(str(exc)) or "temporarily unavailable"}
        )

    @app.exception_handler(GroundtruthError)
    def _handle_terminal(_request: Request, exc: GroundtruthError) -> JSONResponse:
        return JSONResponse(
            status_code=400, content={"detail": _safe_detail(str(exc)) or "bad request"}
        )

    @app.exception_handler(RequestValidationError)
    def _handle_validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"loc": list(err.get("loc", [])), "msg": err.get("msg", "")} for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422, content={"detail": "validation error", "fields": fields}
        )

    return app


# The core engine never sees a request; anonymous is the default principal (§4.5).
DEFAULT_PRINCIPAL = ANONYMOUS

__all__ = ["DEFAULT_PRINCIPAL", "create_app", "outcome_response", "principal_dependency"]
