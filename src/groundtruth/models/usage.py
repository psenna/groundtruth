"""Token-count value object for job records and query results (C7, #116).

A pydantic mirror of :class:`groundtruth.llm.client.TokenUsage` that lives in the
model layer. It deliberately does **not** import from ``llm/``:
:meth:`TokenCounts.from_usage` accepts anything carrying the three integer
attributes (a ``_UsageLike``), so ``models/`` stays free of an ``llm/`` dependency.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict


class _UsageLike(Protocol):
    @property
    def prompt_tokens(self) -> int: ...

    @property
    def completion_tokens(self) -> int: ...

    @property
    def total_tokens(self) -> int: ...


class TokenCounts(BaseModel):
    """Prompt / completion / total token counts for one stage or role."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: TokenCounts) -> TokenCounts:
        return TokenCounts(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )

    @classmethod
    def from_usage(cls, usage: _UsageLike) -> TokenCounts:
        return cls(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
        )


__all__ = ["TokenCounts"]
