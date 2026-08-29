"""Auth primitives shared by API, MCP and web (spec §4.5, ADR-11)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..errors import GroundtruthError


class AuthConfigError(GroundtruthError):
    """The auth configuration is invalid — raised at startup, loudly."""


@dataclass(frozen=True)
class Principal:
    """A resolved caller identity. The core engine only ever sees this — never a request."""

    name: str
    anonymous: bool = False


ANONYMOUS = Principal(name="anonymous", anonymous=True)


class AuthStrategy(Protocol):
    """Turns a raw credential (or its absence) into a principal, or ``None`` to reject."""

    def authenticate(self, credential: str | None) -> Principal | None: ...


__all__ = ["ANONYMOUS", "AuthConfigError", "AuthStrategy", "Principal"]
