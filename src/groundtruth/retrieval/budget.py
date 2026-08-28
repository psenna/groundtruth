"""Budget tracker for the shared agent loop (spec §8.2, §7.4, ADR-6, invariant 4).

Exhaustion is a normal, expected outcome — never an exception. The budget only
*reports* that a limit tripped and which one; the caller decides what it means
(ingestion fails the job, recovery refuses). This module must not assume either.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class _HasLimits(Protocol):
    max_tool_calls: int
    max_wall_clock_s: int
    grep_max_matches: int
    grep_max_bytes: int
    read_max_bytes: int


@dataclass(frozen=True)
class BudgetLimits:
    """Agent-loop limits. Defaults are the §8.2 recovery defaults."""

    max_tool_calls: int = 30
    max_wall_clock_s: int = 60
    grep_max_matches: int = 50
    grep_max_bytes: int = 64 * 1024
    read_max_bytes: int = 32 * 1024

    @classmethod
    def from_limits(cls, limits: _HasLimits) -> BudgetLimits:
        """Build from any object carrying the matching attributes (e.g. ``config.Limits``)."""
        return cls(
            max_tool_calls=limits.max_tool_calls,
            max_wall_clock_s=limits.max_wall_clock_s,
            grep_max_matches=limits.grep_max_matches,
            grep_max_bytes=limits.grep_max_bytes,
            read_max_bytes=limits.read_max_bytes,
        )


@dataclass(frozen=True)
class Clamped[T]:
    """A possibly-truncated value. ``truncated`` is always set — truncation is never silent."""

    value: T
    truncated: bool


def _clamp_text(text: str, cap_bytes: int) -> Clamped[str]:
    encoded = text.encode("utf-8")
    if len(encoded) <= cap_bytes:
        return Clamped(text, truncated=False)
    return Clamped(encoded[:cap_bytes].decode("utf-8", "ignore"), truncated=True)


class Budget:
    """Tracks one agent run's consumption against a set of limits.

    Each run gets its own instance; nothing is shared between concurrent runs.
    """

    def __init__(
        self,
        limits: BudgetLimits | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits or BudgetLimits()
        self._clock = clock
        self._start = clock()
        self._tool_calls = 0
        self._tripped: str | None = None

    def record_tool_call(self) -> None:
        self._tool_calls += 1

    @property
    def tool_calls(self) -> int:
        return self._tool_calls

    def elapsed_s(self) -> float:
        return self._clock() - self._start

    @property
    def exhausted(self) -> bool:
        """True once any loop-ending limit is reached. Latches — never un-trips."""
        if self._tripped is not None:
            return True
        if self._tool_calls >= self.limits.max_tool_calls:
            self._tripped = "max_tool_calls"
        elif self.elapsed_s() >= self.limits.max_wall_clock_s:
            self._tripped = "max_wall_clock_s"
        return self._tripped is not None

    @property
    def tripped_limit(self) -> str | None:
        """Name of the limit that ended the run, or ``None`` if it has not."""
        _ = self.exhausted
        return self._tripped

    def clamp_matches(self, matches: list[str]) -> Clamped[list[str]]:
        kept = list(matches[: self.limits.grep_max_matches])
        return Clamped(kept, truncated=len(kept) < len(matches))

    def clamp_grep_output(self, text: str) -> Clamped[str]:
        return _clamp_text(text, self.limits.grep_max_bytes)

    def clamp_read(self, text: str) -> Clamped[str]:
        return _clamp_text(text, self.limits.read_max_bytes)


__all__ = ["Budget", "BudgetLimits", "Clamped"]
