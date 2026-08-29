from __future__ import annotations

import pytest

from groundtruth.auth import (
    AuthConfigError,
    BearerStrategy,
    Principal,
    build_strategy,
    register,
)

TOKEN = "s3cr3t-static-token-value"


class TestNone:
    def test_resolves_anonymous(self) -> None:
        principal = build_strategy("none").authenticate(None)
        assert isinstance(principal, Principal)
        assert principal.anonymous is True


class TestBearer:
    def _strategy(self) -> BearerStrategy:
        return build_strategy("bearer", {"bearer_token_env": "GT_TOKEN"}, {"GT_TOKEN": TOKEN})  # type: ignore[return-value]

    def test_accepts_configured_token(self) -> None:
        principal = self._strategy().authenticate(f"Bearer {TOKEN}")
        assert principal is not None and principal.anonymous is False

    def test_accepts_bare_token_without_prefix(self) -> None:
        assert self._strategy().authenticate(TOKEN) is not None

    def test_rejects_wrong_token(self) -> None:
        assert self._strategy().authenticate("Bearer nope") is None

    def test_rejects_absent_credential(self) -> None:
        assert self._strategy().authenticate(None) is None

    def test_uses_constant_time_comparison(self) -> None:
        import groundtruth.auth.strategies as mod

        source = mod.__file__
        text = open(source).read()  # noqa: SIM115, PTH123
        assert "compare_digest" in text
        assert "== self._token" not in text and "!= self._token" not in text

    def test_token_never_appears_in_repr_or_str(self) -> None:
        strategy = self._strategy()
        assert TOKEN not in repr(strategy)
        assert TOKEN not in str(strategy)
        assert repr(strategy) == "BearerStrategy(token=***)"

    def test_token_absent_from_config_error(self) -> None:
        with pytest.raises(AuthConfigError) as excinfo:
            build_strategy("bearer", {"bearer_token_env": "MISSING_VAR"}, {})
        assert TOKEN not in str(excinfo.value)
        assert "MISSING_VAR" in str(excinfo.value)  # names the var, not the value


class TestRegistry:
    def test_reads_token_from_env_not_config(self) -> None:
        # a literal token in config is ignored; only the env var name is honored
        with pytest.raises(AuthConfigError):
            build_strategy("bearer", {"bearer_token": TOKEN}, {"GT_TOKEN": TOKEN})

    def test_unknown_strategy_fails_loudly(self) -> None:
        with pytest.raises(AuthConfigError, match="unknown auth strategy"):
            build_strategy("oauth2")

    def test_third_strategy_registers_without_touching_existing_modules(self) -> None:
        class ApiKeyStrategy:
            def authenticate(self, credential: str | None) -> Principal | None:
                return Principal(name="apikey") if credential == "let-me-in" else None

        register("apikey", lambda _c, _e: ApiKeyStrategy())
        strategy = build_strategy("apikey")
        assert strategy.authenticate("let-me-in") is not None
        assert strategy.authenticate("no") is None
