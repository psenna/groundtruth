"""The agent loop shared by both pipelines (spec §7.4, §8, ADR-3).

Dispatch tool calls, accumulate a transcript, enforce the budget, decide
termination. Ingestion and recovery differ only in the prompt and the tool set —
this module contains **no** vault-specific or pipeline-specific logic.

The tool set is responsible for charging the budget on each ``dispatch``; the
loop only reads ``budget.exhausted`` to decide when to stop.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from ..errors import GroundtruthError
from ..llm.client import LLMResponse
from .budget import Budget


class AgentStatus(StrEnum):
    COMPLETED = "completed"
    EXHAUSTED = "exhausted"
    FAILED = "failed"


class ToolSet(Protocol):
    @property
    def schemas(self) -> list[dict[str, Any]]: ...

    def dispatch(self, name: str, arguments: dict[str, Any]) -> str: ...


class LLMLike(Protocol):
    def complete(
        self,
        role: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> LLMResponse: ...


@dataclass(frozen=True)
class ToolInvocation:
    name: str
    arguments: dict[str, Any]
    result: str


@dataclass
class AgentOutcome:
    status: AgentStatus
    final_text: str | None = None
    transcript: list[ToolInvocation] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def _initial_messages(system: str | Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(system, str):
        return [{"role": "system", "content": system}]
    return [dict(message) for message in system]


def _assistant_message(response: LLMResponse) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": response.text}
    if response.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in response.tool_calls
        ]
    return message


def run_agent(
    client: LLMLike,
    role: str,
    system: str | Sequence[Mapping[str, Any]],
    tools: ToolSet,
    budget: Budget,
) -> AgentOutcome:
    """Run the agent loop until the model stops requesting tools or the budget is spent."""
    messages = _initial_messages(system)
    transcript: list[ToolInvocation] = []
    # Safety net: the budget normally bounds this, but a tool set that never
    # charges must not spin forever.
    hard_cap = 2 * budget.limits.max_tool_calls + 10

    for _ in range(hard_cap):
        if budget.exhausted:
            return AgentOutcome(AgentStatus.EXHAUSTED, None, transcript, messages)

        try:
            response = client.complete(role, messages, tools=tools.schemas)
        except GroundtruthError as exc:
            return AgentOutcome(AgentStatus.FAILED, None, transcript, messages, error=str(exc))

        messages.append(_assistant_message(response))

        if not response.tool_calls:
            return AgentOutcome(AgentStatus.COMPLETED, response.text, transcript, messages)

        for call in response.tool_calls:
            try:
                result = tools.dispatch(call.name, call.arguments)
            except Exception as exc:  # tool errors are reported to the model, not raised
                result = f"tool error: {exc}"
            transcript.append(ToolInvocation(call.name, call.arguments, result))
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    return AgentOutcome(
        AgentStatus.FAILED, None, transcript, messages, error="agent loop did not converge"
    )


__all__ = ["AgentOutcome", "AgentStatus", "ToolInvocation", "ToolSet", "run_agent"]
