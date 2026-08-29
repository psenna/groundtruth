"""The two shipped auth strategies: ``none`` and ``bearer`` (spec §4.5)."""

from __future__ import annotations

import hmac

from .models import ANONYMOUS, Principal


class NoneStrategy:
    """No authentication — every caller is anonymous. Default, bound to localhost."""

    def authenticate(self, credential: str | None) -> Principal | None:
        return ANONYMOUS

    def __repr__(self) -> str:
        return "NoneStrategy()"


class BearerStrategy:
    """A single static bearer token, supplied from an environment variable (§11.4)."""

    def __init__(self, token: str) -> None:
        if not token:
            from .models import AuthConfigError

            raise AuthConfigError("bearer token is empty")
        self._token = token

    def authenticate(self, credential: str | None) -> Principal | None:
        if credential is None:
            return None
        offered = credential.removeprefix("Bearer ").strip()
        # Constant-time: compare_digest does not early-exit on the first mismatch.
        if hmac.compare_digest(offered.encode(), self._token.encode()):
            return Principal(name="bearer", anonymous=False)
        return None

    def __repr__(self) -> str:
        return "BearerStrategy(token=***)"


__all__ = ["BearerStrategy", "NoneStrategy"]
