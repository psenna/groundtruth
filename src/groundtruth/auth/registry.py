"""Strategy registry: config name -> AuthStrategy (spec §4.5, ADR-11).

More structure than two strategies need, chosen deliberately so API, MCP and web
cannot diverge and a third strategy is purely additive.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from .models import AuthConfigError, AuthStrategy
from .strategies import BearerStrategy, NoneStrategy

#: ``(config, environ) -> AuthStrategy``.
StrategyFactory = Callable[[Mapping[str, object], Mapping[str, str]], AuthStrategy]

_REGISTRY: dict[str, StrategyFactory] = {}


def register(name: str, factory: StrategyFactory) -> None:
    """Register a strategy factory under ``name``. A third strategy needs only this call."""
    _REGISTRY[name] = factory


def registered_names() -> frozenset[str]:
    return frozenset(_REGISTRY)


def build_strategy(
    name: str,
    config: Mapping[str, object] | None = None,
    environ: Mapping[str, str] | None = None,
) -> AuthStrategy:
    """Resolve a strategy by config name. An unknown name fails here, at startup."""
    if name not in _REGISTRY:
        raise AuthConfigError(f"unknown auth strategy {name!r}; registered: {sorted(_REGISTRY)}")
    return _REGISTRY[name](config or {}, environ or {})


def _build_none(config: Mapping[str, object], environ: Mapping[str, str]) -> AuthStrategy:
    return NoneStrategy()


def _build_bearer(config: Mapping[str, object], environ: Mapping[str, str]) -> AuthStrategy:
    env_name = config.get("bearer_token_env")
    if not isinstance(env_name, str) or not env_name:
        raise AuthConfigError("bearer auth requires 'bearer_token_env' naming an env var (§11.4)")
    token = environ.get(env_name)
    if not token:
        raise AuthConfigError(f"bearer auth: environment variable {env_name!r} is not set")
    return BearerStrategy(token)


register("none", _build_none)
register("bearer", _build_bearer)


__all__ = ["StrategyFactory", "build_strategy", "register", "registered_names"]
