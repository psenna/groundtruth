"""Error taxonomy for the retry policy (spec §12.2).

Every subsystem raises an exception that is *already classified* transient or
terminal, so retry logic never pattern-matches on strings. Boundaries that talk
to the outside world (the LLM client, git) translate foreign exceptions into
these types.

Classification rule: **only a `TransientError` retries.** Anything else —
including a bare `GroundtruthError` or any non-groundtruth exception — is
terminal. Failing loudly beats retrying blindly.
"""

from __future__ import annotations

#: HTTP status codes worth retrying (spec §12.2): rate limit + transient gateway errors.
TRANSIENT_HTTP_STATUS = frozenset({429, 502, 503})


class GroundtruthError(Exception):
    """Root of the error hierarchy. Carries the pipeline stage it occurred in (§7.11)."""

    def __init__(self, message: str = "", *, stage: str | None = None) -> None:
        super().__init__(message)
        self.stage = stage


class TransientError(GroundtruthError):
    """A failure that may succeed on retry — retried up to twice with backoff (§12.2)."""


class TerminalError(GroundtruthError):
    """A failure that will recur identically on retry — fails the job immediately (§12.2)."""


# --- Transient -------------------------------------------------------------------------


class ModelServerConnectionError(TransientError):
    """Connection refused — e.g. a local model server restarting."""


class ReadTimeoutError(TransientError):
    """A read timed out waiting on an upstream response."""


class TransientHTTPError(TransientError):
    """An upstream returned a retryable HTTP status (429/502/503)."""

    def __init__(self, status_code: int, message: str = "", *, stage: str | None = None) -> None:
        if status_code not in TRANSIENT_HTTP_STATUS:
            raise ValueError(
                f"{status_code} is not a transient status; use TerminalHTTPError "
                f"(transient: {sorted(TRANSIENT_HTTP_STATUS)})"
            )
        super().__init__(message or f"HTTP {status_code}", stage=stage)
        self.status_code = status_code


# --- Terminal -------------------------------------------------------------------------


class TerminalHTTPError(TerminalError):
    """An upstream returned a non-retryable HTTP status (not 429/502/503)."""

    def __init__(self, status_code: int, message: str = "", *, stage: str | None = None) -> None:
        super().__init__(message or f"HTTP {status_code}", stage=stage)
        self.status_code = status_code


class WriteValidationError(TerminalError):
    """The write validator rejected a staged change (spec §7.6). Retrying fails identically."""


class GitConflictError(TerminalError):
    """A git conflict or non-fast-forward push (spec §12.2)."""


class DirtyWorkingTreeError(TerminalError):
    """Ingest was attempted into a dirty working tree (spec §7.1, invariant 5)."""


class MalformedLLMOutputError(TerminalError):
    """The LLM produced output that could not be parsed (spec §12.2)."""


# --- Classification -----------------------------------------------------------------


#: Built-in exceptions that unambiguously mean "retry" even before a boundary wraps them.
_TRANSIENT_BUILTINS: tuple[type[BaseException], ...] = (ConnectionRefusedError, TimeoutError)


def is_transient(exc: BaseException) -> bool:
    """Pure classifier: ``True`` only for a known-transient failure (spec §12.2).

    Unrecognized exceptions are terminal.
    """
    if isinstance(exc, GroundtruthError):
        return isinstance(exc, TransientError)
    return isinstance(exc, _TRANSIENT_BUILTINS)
