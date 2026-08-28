from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from groundtruth.config import ModelConfig
from groundtruth.errors import MalformedLLMOutputError, TerminalError, TransientError
from groundtruth.llm.client import LLMClient

KEY_ENV = "GT_LLM_KEY"
KEY_VALUE = "sk-secret-do-not-leak-000"


def _models() -> dict[str, ModelConfig]:
    base = {"base_url": "https://llm.local/v1", "api_key_env": KEY_ENV}
    return {
        "default": ModelConfig(model="default-model", **base),
        "tag": ModelConfig(model="tag-model", **base),
    }


def _ok_body(*, content: str = "hi", tool_calls: list | None = None) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    environ: dict[str, str] | None = None,
) -> LLMClient:
    sleeps: list[float] = []
    client = LLMClient(
        _models(),
        environ={KEY_ENV: KEY_VALUE} if environ is None else environ,
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        backoff_base=0.01,
    )
    client.test_sleeps = sleeps  # type: ignore[attr-defined]
    return client


class TestRoleResolution:
    def test_tag_uses_own_model_inherits_base_and_key(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["model"] = json.loads(request.content)["model"]
            return httpx.Response(200, json=_ok_body())

        _client(handler).complete("tag", [{"role": "user", "content": "x"}])
        assert seen["model"] == "tag-model"
        assert seen["url"] == "https://llm.local/v1/chat/completions"
        assert seen["auth"] == f"Bearer {KEY_VALUE}"

    def test_role_without_override_falls_back_to_default(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["model"] = json.loads(request.content)["model"]
            return httpx.Response(200, json=_ok_body())

        _client(handler).complete("reduce", [{"role": "user", "content": "x"}])
        assert seen["model"] == "default-model"


class TestApiKey:
    def test_key_read_at_call_time_not_stored(self) -> None:
        env = {KEY_ENV: KEY_VALUE}
        client = _client(lambda r: httpx.Response(200, json=_ok_body()), environ=env)
        # No attribute holds the key itself, and repr() never shows it.
        assert not any(v == KEY_VALUE for v in vars(client).values())
        assert not any("key" in name.lower() for name in vars(client))
        assert KEY_VALUE not in repr(client)
        # Rotating the env var takes effect on the next call with no client change.
        env[KEY_ENV] = "sk-rotated-value"
        seen: dict[str, str | None] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json=_ok_body())

        client._client = httpx.Client(transport=httpx.MockTransport(handler))
        client.complete("tag", [{"role": "user", "content": "x"}])
        assert seen["auth"] == "Bearer sk-rotated-value"

    def test_missing_key_still_calls_without_auth(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json=_ok_body())

        _client(handler, environ={}).complete("tag", [{"role": "user", "content": "x"}])
        assert seen["auth"] is None

    def test_key_absent_from_exception_text(self) -> None:
        client = _client(lambda r: httpx.Response(400, json={"error": "bad"}))
        with pytest.raises(TerminalError) as excinfo:
            client.complete("tag", [{"role": "user", "content": "x"}])
        assert KEY_VALUE not in str(excinfo.value)
        assert KEY_VALUE not in repr(excinfo.value)


class TestRetry:
    @pytest.mark.parametrize("status", [429, 502, 503])
    def test_transient_status_retries_twice_then_raises(self, status: int) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(status, json={"error": "later"})

        client = _client(handler)
        with pytest.raises(TransientError):
            client.complete("tag", [{"role": "user", "content": "x"}])
        assert calls["n"] == 3  # initial + 2 retries
        assert len(client.test_sleeps) == 2  # backoff between attempts, never slept for real

    def test_transient_then_success(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(503, json={})
            return httpx.Response(200, json=_ok_body(content="recovered"))

        result = _client(handler).complete("tag", [{"role": "user", "content": "x"}])
        assert result.text == "recovered"
        assert calls["n"] == 2

    def test_connection_error_is_transient(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        with pytest.raises(TransientError):
            _client(handler).complete("tag", [{"role": "user", "content": "x"}])

    def test_terminal_status_does_not_retry(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(400, json={"error": "bad request"})

        with pytest.raises(TerminalError):
            _client(handler).complete("tag", [{"role": "user", "content": "x"}])
        assert calls["n"] == 1


class TestParsing:
    def test_tool_call_response_parses(self) -> None:
        tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "grep", "arguments": '{"pattern": "acme"}'},
            }
        ]
        result = _client(
            lambda r: httpx.Response(200, json=_ok_body(content="", tool_calls=tool_calls))
        ).complete("tag", [{"role": "user", "content": "x"}], tools=[{"type": "function"}])
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "grep"
        assert result.tool_calls[0].arguments == {"pattern": "acme"}

    def test_malformed_tool_arguments_raise_terminal(self) -> None:
        tool_calls = [
            {"id": "c", "type": "function", "function": {"name": "grep", "arguments": "{not json"}}
        ]
        with pytest.raises(MalformedLLMOutputError):
            _client(lambda r: httpx.Response(200, json=_ok_body(tool_calls=tool_calls))).complete(
                "tag", [{"role": "user", "content": "x"}]
            )

    def test_malformed_response_body_raises_terminal(self) -> None:
        with pytest.raises(MalformedLLMOutputError):
            _client(lambda r: httpx.Response(200, json={"unexpected": True})).complete(
                "tag", [{"role": "user", "content": "x"}]
            )

    def test_token_usage_captured(self) -> None:
        result = _client(lambda r: httpx.Response(200, json=_ok_body())).complete(
            "tag", [{"role": "user", "content": "x"}]
        )
        assert result.usage.prompt_tokens == 11
        assert result.usage.completion_tokens == 7
        assert result.usage.total_tokens == 18
