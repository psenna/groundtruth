from __future__ import annotations

import pytest

from groundtruth.errors import (
    DirtyWorkingTreeError,
    GitConflictError,
    GroundtruthError,
    MalformedLLMOutputError,
    ModelServerConnectionError,
    ReadTimeoutError,
    TerminalError,
    TransientError,
    TransientHTTPError,
    WriteValidationError,
    is_transient,
)

TRANSIENT = [
    ModelServerConnectionError("connection refused"),
    TransientHTTPError(429),
    TransientHTTPError(502),
    TransientHTTPError(503),
    ReadTimeoutError("read timed out"),
]

TERMINAL = [
    WriteValidationError("path escapes vault"),
    GitConflictError("non-fast-forward"),
    DirtyWorkingTreeError("uncommitted changes"),
    MalformedLLMOutputError("not valid json"),
]


class TestHierarchy:
    def test_root_is_groundtruth_error(self) -> None:
        for exc in [*TRANSIENT, *TERMINAL]:
            assert isinstance(exc, GroundtruthError)

    def test_transient_subclasses(self) -> None:
        for exc in TRANSIENT:
            assert isinstance(exc, TransientError)
            assert not isinstance(exc, TerminalError)

    def test_terminal_subclasses(self) -> None:
        for exc in TERMINAL:
            assert isinstance(exc, TerminalError)
            assert not isinstance(exc, TransientError)

    def test_error_carries_stage(self) -> None:
        exc = WriteValidationError("bad path", stage="write-validation")
        assert exc.stage == "write-validation"

    def test_stage_is_optional(self) -> None:
        assert GroundtruthError("boom").stage is None


class TestIsTransient:
    @pytest.mark.parametrize("exc", TRANSIENT)
    def test_transient_true(self, exc: Exception) -> None:
        assert is_transient(exc) is True

    @pytest.mark.parametrize("exc", TERMINAL)
    def test_terminal_false(self, exc: Exception) -> None:
        assert is_transient(exc) is False

    def test_unrecognized_exception_is_terminal(self) -> None:
        assert is_transient(RuntimeError("who knows")) is False
        assert is_transient(ValueError("nope")) is False

    def test_bare_groundtruth_error_is_terminal(self) -> None:
        assert is_transient(GroundtruthError("unclassified")) is False

    def test_is_transient_is_pure(self) -> None:
        exc = TransientHTTPError(503)
        assert is_transient(exc) == is_transient(exc)


class TestTransientHTTPError:
    def test_carries_status_code(self) -> None:
        assert TransientHTTPError(429).status_code == 429

    def test_rejects_non_transient_status(self) -> None:
        with pytest.raises(ValueError):
            TransientHTTPError(404)
