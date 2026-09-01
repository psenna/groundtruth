from __future__ import annotations

import pytest
from pydantic import ValidationError

from groundtruth.models import AnswerResult, Citation, Refusal
from groundtruth.recovery.format import render_answer, render_refusal, to_payload


def _answer() -> AnswerResult:
    return AnswerResult(
        text="Acme [[companies/Acme]] was founded in 1996 and rivals [[companies/Globex]].",
        citations=[
            Citation(vault="work", path="companies/Acme"),
            Citation(vault="work", path="companies/Globex"),
        ],
    )


class TestRenderAnswer:
    def test_renders_markdown_with_flat_citations(self) -> None:
        out = render_answer(_answer())
        assert "[[companies/Acme]]" in out
        assert "[[companies/Globex]]" in out
        assert ":" not in out.split("[[")[1].split("]]")[0]  # no vault in the flat form

    def test_qualified_form_is_reachable_without_changing_the_model_layer(self) -> None:
        answer = _answer()
        qualified = render_answer(answer, qualified=True)
        assert "[[work:companies/Acme]]" in qualified
        assert "[[work:companies/Globex]]" in qualified
        # the AnswerResult is untouched
        assert answer.text == _answer().text
        assert all(":" not in c.path for c in answer.citations)

    def test_every_substantive_claim_carries_a_citation(self) -> None:
        # both claims in the fixture answer have a [[...]] adjacent
        out = render_answer(_answer())
        assert out.count("[[") == 2


class TestRefusalTypes:
    def test_only_two_reasons(self) -> None:
        assert Refusal(reason="no_evidence").reason == "no_evidence"
        assert Refusal(reason="budget_exhausted").reason == "budget_exhausted"
        with pytest.raises(ValidationError):
            Refusal(reason="something_else")  # type: ignore[arg-type]

    def test_the_two_refusals_are_structurally_identical(self) -> None:
        a = Refusal(reason="no_evidence")
        b = Refusal(reason="budget_exhausted")
        assert type(a) is type(b)
        assert Refusal.model_fields.keys() == {"kind", "reason", "token_usage"}
        assert a.model_dump().keys() == b.model_dump().keys()
        assert {k: v for k, v in a.model_dump().items() if k != "reason"} == {
            k: v for k, v in b.model_dump().items() if k != "reason"
        }

    def test_budget_message_matches_spec_8_2(self) -> None:
        assert render_refusal(Refusal(reason="budget_exhausted")) == (
            "Could not establish ground truth for this question within the search budget."
        )


class TestNoPartialAnswer:
    def test_no_refusal_path_emits_findings_or_a_caveat(self) -> None:
        for reason in ("no_evidence", "budget_exhausted"):
            message = render_refusal(Refusal(reason=reason))  # type: ignore[arg-type]
            lowered = message.lower()
            assert "[[" not in message
            for banned in ("however", "partial", "some evidence", "warning", "but ", "although"):
                assert banned not in lowered

    def test_refusal_payload_has_no_answer_fields(self) -> None:
        payload = to_payload(Refusal(reason="no_evidence"))
        assert payload["outcome"] == "refused"
        assert "text" not in payload
        assert "citations" not in payload
        assert "findings" not in payload

    def test_answer_payload_shape(self) -> None:
        payload = to_payload(_answer())
        assert payload["outcome"] == "answer"
        assert payload["citations"] == [
            {"vault": "work", "path": "companies/Acme"},
            {"vault": "work", "path": "companies/Globex"},
        ]


class TestTokenUsageInPayload:
    def test_answer_payload_carries_token_usage(self) -> None:
        from groundtruth.models import TokenCounts

        answer = _answer().model_copy(
            update={"token_usage": {"answer": TokenCounts(prompt_tokens=7, total_tokens=7)}}
        )
        payload = to_payload(answer)
        assert payload["token_usage"] == {
            "answer": {"prompt_tokens": 7, "completion_tokens": 0, "total_tokens": 7}
        }

    def test_refusal_payload_carries_token_usage(self) -> None:
        from groundtruth.models import TokenCounts

        counts = TokenCounts(prompt_tokens=3, completion_tokens=2, total_tokens=5)
        refusal = Refusal(reason="no_evidence", token_usage={"answer": counts})
        payload = to_payload(refusal)
        assert payload["token_usage"]["answer"]["total_tokens"] == 5
