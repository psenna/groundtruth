from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .usage import TokenCounts

RefusalReason = Literal["no_evidence", "budget_exhausted"]


class Citation(BaseModel):
    """A structured pointer to evidence — ``{vault, path}``, never a bare string (spec §8.3).

    The vault is carried even though MVP queries target one vault, so cross-vault
    would change only rendering.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    vault: str
    #: Vault-relative path of the cited note.
    path: str


class AnswerResult(BaseModel):
    """A grounded answer: Markdown prose plus the citations backing it (spec §8.3)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["answer"] = "answer"
    text: str
    citations: list[Citation]
    #: Token usage of the query that produced this answer, keyed by stage
    #: (currently just ``answer``). Cost accounting only — never a finding.
    token_usage: dict[str, TokenCounts] = Field(default_factory=dict)


class Refusal(BaseModel):
    """A first-class "no answer" outcome, not an error (spec §8.4).

    Returned with HTTP 200 and a structured reason.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["refusal"] = "refusal"
    reason: RefusalReason
    #: Token usage of the query that led to this refusal (see ``AnswerResult``).
    token_usage: dict[str, TokenCounts] = Field(default_factory=dict)


#: The recovery pipeline returns exactly one of these.
RecoveryOutcome = AnswerResult | Refusal
