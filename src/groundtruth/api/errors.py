"""Adapter-layer HTTP error helper (shared by the API routers)."""

from __future__ import annotations

from typing import NoReturn


class ApiError(Exception):
    """An adapter-level failure that maps directly to an HTTP status."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def problem(status: int, detail: str) -> NoReturn:
    """Raise an :class:`ApiError` — e.g. ``problem(422, "schema.md is missing")``."""
    raise ApiError(status, detail)


__all__ = ["ApiError", "problem"]
