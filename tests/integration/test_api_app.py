from __future__ import annotations

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import BaseModel

from groundtruth.api.app import create_app, outcome_response
from groundtruth.auth import build_strategy
from groundtruth.errors import (
    MalformedLLMOutputError,
    ModelServerConnectionError,
    WriteValidationError,
)
from groundtruth.models import AnswerResult, Citation, Refusal

TOKEN = "static-bearer-token-abc123"


def _router() -> APIRouter:
    router = APIRouter()

    @router.get("/refuse")
    def refuse() -> object:
        return outcome_response(Refusal(reason="no_evidence"))

    @router.get("/answer")
    def answer() -> object:
        return outcome_response(
            AnswerResult(text="Founded 1996. [[a]]", citations=[Citation(vault="work", path="a")])
        )

    @router.get("/terminal")
    def terminal() -> object:
        raise WriteValidationError(
            "path /home/node/secret/vault escapes; token sk-ABCDEF0123456789"
        )

    @router.get("/malformed")
    def malformed() -> object:
        raise MalformedLLMOutputError("not json")

    @router.get("/transient")
    def transient() -> object:
        raise ModelServerConnectionError("connection refused to /run/model.sock")

    class Body(BaseModel):
        n: int

    @router.post("/needs-int")
    def needs_int(body: Body) -> dict[str, int]:
        return {"n": body.n}

    return router


@pytest.fixture
def anon_client() -> TestClient:
    return TestClient(create_app(auth=build_strategy("none"), routers=[_router()]))


@pytest.fixture
def bearer_client() -> TestClient:
    app = create_app(
        auth=build_strategy("bearer", {"bearer_token_env": "GT_T"}, {"GT_T": TOKEN}),
        routers=[_router()],
    )
    return TestClient(app)


class TestRefusalIsSuccess:
    def test_refusal_is_200_with_structured_reason(self, anon_client: TestClient) -> None:
        resp = anon_client.get("/refuse")
        assert resp.status_code == 200
        assert resp.json() == {
            "outcome": "refused",
            "reason": "no_evidence",
            "message": "The vault does not contain information to answer this question.",
        }

    def test_answer_is_200_same_shape_family(self, anon_client: TestClient) -> None:
        body = anon_client.get("/answer").json()
        assert body["outcome"] == "answer"
        assert body["citations"] == [{"vault": "work", "path": "a"}]


class TestErrorMapping:
    def test_terminal_error_maps_to_4xx(self, anon_client: TestClient) -> None:
        resp = anon_client.get("/terminal")
        assert 400 <= resp.status_code < 500

    def test_malformed_output_is_4xx(self, anon_client: TestClient) -> None:
        assert anon_client.get("/malformed").status_code == 400

    def test_transient_error_maps_to_503(self, anon_client: TestClient) -> None:
        assert anon_client.get("/transient").status_code == 503

    def test_no_error_response_leaks_a_secret_or_absolute_path(
        self, anon_client: TestClient
    ) -> None:
        for path in ("/terminal", "/transient"):
            text = anon_client.get(path).text
            assert "sk-ABCDEF0123456789" not in text
            assert "/home/node/secret" not in text
            assert "/run/model.sock" not in text

    def test_validation_error_is_422_with_field_detail(self, anon_client: TestClient) -> None:
        resp = anon_client.post("/needs-int", json={"n": "not-an-int"})
        assert resp.status_code == 422
        body = resp.json()
        assert body["fields"] and body["fields"][0]["loc"]


class TestAuth:
    def test_health_needs_no_auth(self, bearer_client: TestClient) -> None:
        assert bearer_client.get("/health").json() == {"status": "ok"}

    def test_protected_route_rejects_missing_token(self, bearer_client: TestClient) -> None:
        assert bearer_client.get("/refuse").status_code == 401

    def test_protected_route_accepts_valid_token(self, bearer_client: TestClient) -> None:
        resp = bearer_client.get("/refuse", headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status_code == 200

    def test_anonymous_principal_by_default(self, anon_client: TestClient) -> None:
        assert anon_client.get("/whoami").json() == {"name": "anonymous", "anonymous": True}
