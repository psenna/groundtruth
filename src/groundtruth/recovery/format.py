"""User-facing rendering of both recovery outcomes (spec §8.3, §8.4, ADR-6).

One module for all three surfaces (API, MCP, web) — they must not each invent
their own. A refusal is a first-class result, not an error: the two refusal
reasons are structurally identical (same type, same shape), differing only in
``reason``, and **no refusal path ever emits a partial finding, a caveat, or a
warning banner**.
"""

from __future__ import annotations

import re
from typing import Any

from ..models import AnswerResult, Refusal

#: Verbatim from §8.2.
_BUDGET_EXHAUSTED_MESSAGE = (
    "Could not establish ground truth for this question within the search budget."
)
_NO_EVIDENCE_MESSAGE = "The vault does not contain information to answer this question."

_REFUSAL_MESSAGES = {
    "budget_exhausted": _BUDGET_EXHAUSTED_MESSAGE,
    "no_evidence": _NO_EVIDENCE_MESSAGE,
}


def render_answer(answer: AnswerResult, *, qualified: bool = False) -> str:
    """Render an answer as Markdown.

    Citations are stored as ``{vault, path}`` but render **flat** as ``[[path]]``.
    Rendering is the only place the vault is dropped; ``qualified=True`` produces
    ``[[vault:path]]`` without touching the model layer.
    """
    text = answer.text
    if qualified:
        for citation in answer.citations:
            pattern = re.compile(r"\[\[" + re.escape(citation.path) + r"(\||\]\])")
            text = pattern.sub(f"[[{citation.vault}:{citation.path}" + r"\1", text)
    return text


def render_refusal(refusal: Refusal) -> str:
    """Render a refusal message. No caveat, no partial findings, no banner."""
    return _REFUSAL_MESSAGES[refusal.reason]


def to_payload(result: AnswerResult | Refusal) -> dict[str, Any]:
    """Structured shape for API and MCP responses (both outcomes are HTTP 200)."""
    if isinstance(result, Refusal):
        return {"outcome": "refused", "reason": result.reason, "message": render_refusal(result)}
    return {
        "outcome": "answer",
        "text": render_answer(result),
        "citations": [{"vault": c.vault, "path": c.path} for c in result.citations],
    }


__all__ = ["render_answer", "render_refusal", "to_payload"]
