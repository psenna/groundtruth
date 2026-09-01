from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from groundtruth.llm.client import ZERO_USAGE, LLMResponse, TokenUsage, ToolCall
from groundtruth.retrieval.agent import AgentStatus, run_agent
from groundtruth.retrieval.budget import Budget, BudgetLimits


class ScriptedClient:
    """Returns the next queued LLMResponse on each complete() call."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    def complete(
        self,
        role: str,
        messages: Sequence[Any],
        *,
        tools: Sequence[Any] | None = None,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        return self._responses.pop(0)


def _say(text: str) -> LLMResponse:
    return LLMResponse(role="answer", model="m", text=text, usage=TokenUsage(1, 1, 2))


def _call(name: str, args: dict[str, Any], *, call_id: str = "c1") -> LLMResponse:
    return LLMResponse(
        role="answer",
        model="m",
        text=None,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        usage=TokenUsage(1, 1, 2),
    )


class FakeTools:
    """Minimal ToolSet: charges the shared budget, dispatches by name."""

    def __init__(self, budget: Budget, funcs: dict[str, Any]) -> None:
        self._budget = budget
        self._funcs = funcs

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {"name": n}} for n in self._funcs]

    def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        if self._budget.exhausted:
            return "[budget exhausted]"
        self._budget.record_tool_call()
        if name not in self._funcs:
            return f"unknown tool: {name}"
        return self._funcs[name](**arguments)


def test_loop_returns_final_message_when_model_stops() -> None:
    budget = Budget(BudgetLimits(max_tool_calls=10))
    tools = FakeTools(budget, {"ls": lambda: "a\nb"})
    client = ScriptedClient([_call("ls", {}), _say("done: a, b")])

    outcome = run_agent(client, "answer", "you are a test agent", tools, budget)

    assert outcome.status is AgentStatus.COMPLETED
    assert outcome.final_text == "done: a, b"
    assert len(client.calls) == 2


def test_string_prompt_is_a_user_message_not_system() -> None:
    # Some chat templates (Qwen, some Llama/Mistral) 500 on a messages array
    # with no user turn — the loop's opening message must be role "user".
    budget = Budget(BudgetLimits(max_tool_calls=10))
    tools = FakeTools(budget, {})
    client = ScriptedClient([_say("ok")])

    run_agent(client, "answer", "survey this text", tools, budget)

    assert client.calls[0][0] == {"role": "user", "content": "survey this text"}


def test_transcript_records_calls_and_results_in_order() -> None:
    budget = Budget(BudgetLimits(max_tool_calls=10))
    tools = FakeTools(budget, {"ls": lambda: "listing", "read": lambda path: f"body of {path}"})
    client = ScriptedClient(
        [_call("ls", {}, call_id="1"), _call("read", {"path": "n.md"}, call_id="2"), _say("ok")]
    )

    outcome = run_agent(client, "answer", "sys", tools, budget)

    assert [(t.name, t.result) for t in outcome.transcript] == [
        ("ls", "listing"),
        ("read", "body of n.md"),
    ]


def test_budget_exhaustion_terminates_cleanly() -> None:
    budget = Budget(BudgetLimits(max_tool_calls=2))
    tools = FakeTools(budget, {"ls": lambda: "x"})
    client = ScriptedClient([_call("ls", {}), _call("ls", {}), _call("ls", {}), _say("never")])

    outcome = run_agent(client, "answer", "sys", tools, budget)

    assert outcome.status is AgentStatus.EXHAUSTED
    assert outcome.final_text is None
    assert budget.tool_calls == 2


def test_unknown_tool_reported_not_crashed() -> None:
    budget = Budget(BudgetLimits(max_tool_calls=10))
    tools = FakeTools(budget, {"ls": lambda: "x"})
    client = ScriptedClient([_call("teleport", {}), _say("recovered")])

    outcome = run_agent(client, "answer", "sys", tools, budget)

    assert outcome.status is AgentStatus.COMPLETED
    assert "unknown tool" in outcome.transcript[0].result


def test_tool_raising_is_reported_and_loop_continues() -> None:
    budget = Budget(BudgetLimits(max_tool_calls=10))

    def boom() -> str:
        raise RuntimeError("kaboom")

    tools = FakeTools(budget, {"boom": boom, "ls": lambda: "ok"})
    client = ScriptedClient([_call("boom", {}), _call("ls", {}), _say("continued past error")])

    outcome = run_agent(client, "answer", "sys", tools, budget)

    assert outcome.status is AgentStatus.COMPLETED
    assert "kaboom" in outcome.transcript[0].result
    assert outcome.transcript[1].result == "ok"


def test_generic_over_tool_sets() -> None:
    budget_ro = Budget(BudgetLimits(max_tool_calls=10))
    read_only = FakeTools(budget_ro, {"read": lambda path: "ro"})
    out_ro = run_agent(
        ScriptedClient([_call("read", {"path": "x"}), _say("ro done")]),
        "answer",
        "sys",
        read_only,
        budget_ro,
    )
    assert out_ro.status is AgentStatus.COMPLETED

    budget_rw = Budget(BudgetLimits(max_tool_calls=10))
    read_write = FakeTools(budget_rw, {"read": lambda path: "rw", "write": lambda **kw: "written"})
    out_rw = run_agent(
        ScriptedClient([_call("write", {"path": "x", "body": "b"}), _say("rw done")]),
        "answer",
        "sys",
        read_write,
        budget_rw,
    )
    assert out_rw.status is AgentStatus.COMPLETED
    assert out_rw.transcript[0].result == "written"


def test_usage_accumulates_across_turns() -> None:
    budget = Budget(BudgetLimits(max_tool_calls=10))
    tools = FakeTools(budget, {"ls": lambda: "a\nb"})
    client = ScriptedClient([_call("ls", {}), _say("done: a, b")])

    outcome = run_agent(client, "answer", "sys", tools, budget)

    # two client.complete calls, each carrying TokenUsage(1, 1, 2)
    assert outcome.usage == TokenUsage(2, 2, 4)


def test_usage_defaults_to_zero_and_is_zero_when_exhausted_first() -> None:
    from groundtruth.retrieval.agent import AgentOutcome

    assert AgentOutcome(AgentStatus.COMPLETED).usage is ZERO_USAGE

    budget = Budget(BudgetLimits(max_tool_calls=0))
    tools = FakeTools(budget, {})
    outcome = run_agent(ScriptedClient([]), "answer", "sys", tools, budget)
    assert outcome.status is AgentStatus.EXHAUSTED
    assert outcome.usage == ZERO_USAGE


def test_usage_is_carried_on_a_failed_outcome() -> None:
    from groundtruth.errors import MalformedLLMOutputError

    class OneThenFail:
        def __init__(self) -> None:
            self.n = 0

        def complete(self, *a: Any, **k: Any) -> LLMResponse:
            self.n += 1
            if self.n == 1:
                return _call("ls", {})
            raise MalformedLLMOutputError("bad json")

    budget = Budget(BudgetLimits(max_tool_calls=10))
    tools = FakeTools(budget, {"ls": lambda: "x"})
    outcome = run_agent(OneThenFail(), "answer", "sys", tools, budget)
    assert outcome.status is AgentStatus.FAILED
    assert outcome.usage == TokenUsage(1, 1, 2)


def test_llm_failure_is_a_failed_outcome() -> None:
    from groundtruth.errors import MalformedLLMOutputError

    class FailingClient:
        def complete(self, *a: Any, **k: Any) -> LLMResponse:
            raise MalformedLLMOutputError("bad json")

    budget = Budget(BudgetLimits())
    tools = FakeTools(budget, {})
    outcome = run_agent(FailingClient(), "answer", "sys", tools, budget)
    assert outcome.status is AgentStatus.FAILED
    assert "bad json" in (outcome.error or "")
