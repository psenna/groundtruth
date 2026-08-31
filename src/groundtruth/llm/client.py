"""OpenAI-compatible LLM client with per-role models (spec §4.3, §11.2).

Works against anything exposing the OpenAI chat-completions API (OpenAI, Ollama,
vLLM, …). Retry classification is delegated to :mod:`groundtruth.errors` (#5) —
this module does not re-match error shapes. The API key is read from the
environment at call time and never stored on the client.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import ModelConfig
from ..errors import (
    TRANSIENT_HTTP_STATUS,
    GroundtruthError,
    MalformedLLMOutputError,
    ModelServerConnectionError,
    ReadTimeoutError,
    TerminalHTTPError,
    TransientHTTPError,
    is_transient,
)

_STAGE = "llm"


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


_ZERO_USAGE = TokenUsage(0, 0, 0)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    role: str
    model: str
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage = _ZERO_USAGE


class LLMClient:
    """Chat-completions client. ``models`` is keyed by role and must contain ``default``."""

    def __init__(
        self,
        models: Mapping[str, ModelConfig],
        *,
        environ: Mapping[str, str],
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 2,
        backoff_base: float = 0.5,
        timeout: float = 60.0,
    ) -> None:
        if "default" not in models:
            raise ValueError("models must contain a 'default' role")
        self._models = dict(models)
        self._environ = environ
        self._sleep = sleep
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._client = httpx.Client(transport=transport, timeout=timeout)

    def __repr__(self) -> str:
        return f"LLMClient(roles={sorted(self._models)})"

    def model_for(self, role: str) -> ModelConfig:
        return self._models.get(role, self._models["default"])

    def complete(
        self,
        role: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> LLMResponse:
        model = self.model_for(role)
        payload: dict[str, Any] = {"model": model.model, "messages": list(messages)}
        if model.reasoning_effort is not None:
            payload["reasoning_effort"] = model.reasoning_effort
        if tools:
            payload["tools"] = list(tools)

        for attempt in range(self._max_retries + 1):
            try:
                return self._request(role, model, payload)
            except GroundtruthError as exc:
                if not is_transient(exc) or attempt == self._max_retries:
                    raise
                self._sleep(self._backoff_base * (2**attempt))
        raise AssertionError("unreachable")  # pragma: no cover

    def _request(self, role: str, model: ModelConfig, payload: dict[str, Any]) -> LLMResponse:
        headers: dict[str, str] = {}
        api_key = model.resolve_api_key(self._environ)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        url = model.base_url.rstrip("/") + "/chat/completions"
        try:
            response = self._client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise ReadTimeoutError(f"timed out calling {url}", stage=_STAGE) from exc
        except httpx.TransportError as exc:
            raise ModelServerConnectionError(f"could not reach {url}", stage=_STAGE) from exc

        self._raise_for_status(response.status_code)
        return self._parse(role, model, response)

    @staticmethod
    def _raise_for_status(status: int) -> None:
        if status < 400:
            return
        if status in TRANSIENT_HTTP_STATUS:
            raise TransientHTTPError(status, stage=_STAGE)
        raise TerminalHTTPError(status, f"LLM returned HTTP {status}", stage=_STAGE)

    @staticmethod
    def _parse(role: str, model: ModelConfig, response: httpx.Response) -> LLMResponse:
        try:
            body = response.json()
            message = body["choices"][0]["message"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise MalformedLLMOutputError(
                f"LLM response missing choices/message: {exc}", stage=_STAGE
            ) from exc

        tool_calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            try:
                fn = raw["function"]
                arguments = json.loads(fn["arguments"]) if fn.get("arguments") else {}
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise MalformedLLMOutputError(
                    f"LLM tool call is not well-formed: {exc}", stage=_STAGE
                ) from exc
            tool_calls.append(ToolCall(id=raw.get("id", ""), name=fn["name"], arguments=arguments))

        usage_raw = body.get("usage") or {}
        usage = TokenUsage(
            prompt_tokens=int(usage_raw.get("prompt_tokens", 0)),
            completion_tokens=int(usage_raw.get("completion_tokens", 0)),
            total_tokens=int(usage_raw.get("total_tokens", 0)),
        )
        return LLMResponse(
            role=role,
            model=model.model,
            text=message.get("content"),
            tool_calls=tool_calls,
            usage=usage,
        )


__all__ = ["LLMClient", "LLMResponse", "TokenUsage", "ToolCall"]
