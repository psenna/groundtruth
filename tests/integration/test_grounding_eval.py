"""Golden eval — layer 2 of grounding verification (spec §9.2).

The must-refuse cases are the point: the only mechanical way to catch the model
quietly filling a gap from pretraining. A correct-but-ungrounded answer is a
FAILURE.

The default run is **deterministic** — a scripted fake model, no network. Set
``GT_EVAL_MODEL_BASE_URL`` etc. and select ``-m eval_live`` to run the same
questions against a real OpenAI-compatible model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from groundtruth.config import Limits
from groundtruth.llm.client import LLMClient, LLMResponse, ToolCall
from groundtruth.models import AnswerResult, Refusal, Vault
from groundtruth.recovery.agent import recover
from groundtruth.recovery.grounding import check_grounding
from groundtruth.retrieval.agent import AgentStatus

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "vault"
_VAULT = Vault(name="vault", repo_root=_FIXTURE)


@dataclass
class Case:
    id: str
    question: str
    fact: str = ""  # substring the answer must contain (answerable only)
    cite: str = ""  # note the answer must cite (answerable only)
    # scripted fake-model turns for this question (default deterministic run)
    script: list[LLMResponse] = field(default_factory=list)


def _grep(pattern: str) -> LLMResponse:
    return LLMResponse(
        role="answer",
        model="fake",
        text=None,
        tool_calls=[ToolCall(id="c", name="grep", arguments={"pattern": pattern})],
    )


def _say(text: str) -> LLMResponse:
    return LLMResponse(role="answer", model="fake", text=text)


ANSWERABLE = [
    Case(
        "northwind-founded",
        "When was Northwind Traders founded?",
        fact="2011",
        cite="companies/Northwind Traders",
        script=[
            _grep("founded"),
            _say("Northwind Traders was founded in 2011. [[companies/Northwind Traders]]"),
        ],
    ),
    Case(
        "migration-lead",
        "Who leads the Warehouse Migration project?",
        fact="Sam Okafor",
        cite="projects/Warehouse Migration",
        script=[_grep("Warehouse"), _say("Sam Okafor leads it. [[projects/Warehouse Migration]]")],
    ),
    Case(
        "contract-value",
        "What is the annual value of the Northwind Contract?",
        fact="120000",
        cite="companies/Northwind Contract",
        script=[_grep("value"), _say("120000 USD per year. [[companies/Northwind Contract]]")],
    ),
    Case(
        "prev-supplier",
        "Who supplied packaging before Fabrikam Inc?",
        fact="Tailspin",
        cite="companies/Tailspin Toys",
        script=[_grep("supplier"), _say("Tailspin Toys. [[companies/Tailspin Toys]]")],
    ),
]

MUST_REFUSE = [
    # facts a pretrained model plausibly knows, absent from the fixture:
    Case(
        "microsoft-founded",
        "When was Microsoft founded?",
        script=[_grep("Microsoft"), _say("Microsoft was founded in 1975. [[companies/Microsoft]]")],
    ),
    Case(
        "portland-population",
        "What is the population of Portland?",
        script=[_grep("Portland"), _say("About 650,000 people. [[companies/Portland]]")],
    ),
    # information genuinely not in the vault:
    Case(
        "contoso-ceo",
        "Who is the CEO of Contoso Ltd?",
        script=[_grep("CEO"), _say("The vault does not contain Contoso's CEO.")],
    ),
    Case(
        "northwind-stock",
        "What is Northwind Traders' stock price?",
        script=[_grep("stock"), _say("There is no information about a stock price in the vault.")],
    ),
]


def _fake_model(script: list[LLMResponse]):  # type: ignore[no-untyped-def]
    remaining = list(script)

    class _M:
        def complete(self, role, messages, **kw):  # type: ignore[no-untyped-def]
            return remaining.pop(0) if remaining else _say("(no more scripted turns)")

    return _M()


def _resolve(question: str, model, *, limits: Limits | None = None) -> AnswerResult | Refusal:
    outcome = recover(_VAULT, question, model, limits=limits)
    if outcome.status is AgentStatus.EXHAUSTED:
        return Refusal(reason="budget_exhausted")
    assert outcome.status is AgentStatus.COMPLETED, outcome.error
    return check_grounding(AnswerResult(text=outcome.final_text or "", citations=[]), _VAULT)


@pytest.mark.parametrize("case", ANSWERABLE, ids=lambda c: c.id)
def test_answerable(case: Case) -> None:
    result = _resolve(case.question, _fake_model(case.script))
    assert isinstance(result, AnswerResult), f"{case.id}: expected an answer"
    assert case.fact in result.text, f"{case.id}: answer missing the fact"
    assert any(c.path == case.cite for c in result.citations), (
        f"{case.id}: missing citation {case.cite}"
    )


@pytest.mark.parametrize("case", MUST_REFUSE, ids=lambda c: c.id)
def test_must_refuse(case: Case) -> None:
    result = _resolve(case.question, _fake_model(case.script))
    assert isinstance(result, Refusal), (
        f"{case.id}: got an answer for a question the vault cannot answer — "
        "a correct-but-ungrounded answer is a failure (§9.2)"
    )


def test_a_citation_to_a_nonexistent_note_fails_the_suite() -> None:
    # If the model cites a note that is not on disk, the suite must catch it.
    bad = _fake_model([_say("Yes. [[companies/Does Not Exist]]")])
    result = _resolve("anything", bad)
    assert isinstance(result, Refusal)  # grounding downgrades it; the answerable assert would fail


def test_budget_exhaustion_is_a_refusal_with_a_tiny_budget() -> None:
    tiny = Limits(
        max_notes_per_ingest=10,
        max_note_bytes=65536,
        max_tool_calls=1,
        max_wall_clock_s=60,
        grep_max_matches=50,
        grep_max_bytes=65536,
        read_max_bytes=32768,
        vocab_max_bytes=4096,
    )
    model = _fake_model([_grep("x")] * 5)
    result = _resolve("When was Northwind Traders founded?", model, limits=tiny)
    assert isinstance(result, Refusal)
    assert result.reason == "budget_exhausted"


@pytest.mark.eval_live
@pytest.mark.parametrize("case", [*ANSWERABLE, *MUST_REFUSE], ids=lambda c: c.id)
def test_against_a_real_model(case: Case) -> None:
    base_url = os.environ.get("GT_EVAL_MODEL_BASE_URL")
    if not base_url:
        pytest.skip("set GT_EVAL_MODEL_BASE_URL to run the live eval")
    from groundtruth.config import ModelConfig

    model = LLMClient(
        {
            "default": ModelConfig(
                base_url=base_url,
                model=os.environ.get("GT_EVAL_MODEL", "qwen2.5:14b"),
                api_key_env=os.environ.get("GT_EVAL_API_KEY_ENV", "GT_API_KEY"),
            )
        },
        environ=os.environ,
    )
    result = _resolve(case.question, model)
    if case in ANSWERABLE:
        assert isinstance(result, AnswerResult) and case.fact in result.text
    else:
        assert isinstance(result, Refusal)
