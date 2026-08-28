from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from groundtruth.config import (
    BUILTIN_DEFAULTS,
    ConfigError,
    load_vault_config,
    resolve_global_config_path,
)


def _write(path: Path, data: dict[str, object]) -> Path:
    path.write_text(yaml.safe_dump(data))
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "work-repo"
    (root / "work").mkdir(parents=True)
    return root


def _global(tmp_path: Path, repo: Path, **defaults: object) -> Path:
    return _write(
        tmp_path / "config.yaml",
        {
            "state_dir": str(tmp_path / "state"),
            "vaults": {"work": str(repo)},
            "defaults": defaults,
        },
    )


class TestPrecedence:
    def test_builtin_defaults_when_nothing_set(self, tmp_path: Path, repo: Path) -> None:
        cfg_path = _global(tmp_path, repo)
        cfg = load_vault_config("work", cli_config=cfg_path, environ={})
        assert cfg.raw_archive is BUILTIN_DEFAULTS["raw_archive"]
        assert cfg.auto_push is BUILTIN_DEFAULTS["auto_push"]

    def test_global_overrides_builtin(self, tmp_path: Path, repo: Path) -> None:
        cfg_path = _global(tmp_path, repo, auto_push=True)
        cfg = load_vault_config("work", cli_config=cfg_path, environ={})
        assert cfg.auto_push is True

    def test_per_vault_overrides_global(self, tmp_path: Path, repo: Path) -> None:
        cfg_path = _global(tmp_path, repo, auto_push=True, raw_archive=True)
        _write(repo / ".groundtruth.yaml", {"raw_archive": False})
        cfg = load_vault_config("work", cli_config=cfg_path, environ={})
        assert cfg.raw_archive is False  # per-vault wins
        assert cfg.auto_push is True  # still inherited from global

    def test_all_three_levels_named(self, tmp_path: Path, repo: Path) -> None:
        # builtin: allow_schema_writes=False ; global sets auto_push ; vault sets raw_archive
        cfg_path = _global(tmp_path, repo, auto_push=True)
        _write(repo / ".groundtruth.yaml", {"raw_archive": False})
        cfg = load_vault_config("work", cli_config=cfg_path, environ={})
        assert cfg.allow_schema_writes is False  # builtin
        assert cfg.auto_push is True  # global
        assert cfg.raw_archive is False  # per-vault


class TestPerKeyMerge:
    def test_vault_overriding_one_model_keeps_the_others(self, tmp_path: Path, repo: Path) -> None:
        cfg_path = _global(tmp_path, repo)
        _write(repo / ".groundtruth.yaml", {"models": {"answer": {"model": "gpt-4o"}}})
        cfg = load_vault_config("work", cli_config=cfg_path, environ={})
        assert cfg.models["answer"].model == "gpt-4o"  # overridden
        assert cfg.models["tag"].model == BUILTIN_DEFAULTS["models"]["tag"]["model"]  # inherited

    def test_role_inherits_base_url_and_api_key_env_from_default(
        self, tmp_path: Path, repo: Path
    ) -> None:
        cfg_path = _global(tmp_path, repo)
        cfg = load_vault_config("work", cli_config=cfg_path, environ={})
        assert cfg.models["tag"].base_url == cfg.models["default"].base_url
        assert cfg.models["tag"].api_key_env == cfg.models["default"].api_key_env


class TestGlobalConfigPath:
    def test_cli_config_wins(self, tmp_path: Path) -> None:
        p = tmp_path / "cli.yaml"
        p.touch()
        assert resolve_global_config_path(cli_config=p, environ={"GT_CONFIG": "/x"}) == p

    def test_env_var_second(self, tmp_path: Path) -> None:
        p = tmp_path / "env.yaml"
        assert resolve_global_config_path(cli_config=None, environ={"GT_CONFIG": str(p)}) == p

    def test_default_path_last(self) -> None:
        assert resolve_global_config_path(cli_config=None, environ={}) == Path(
            "/etc/groundtruth/config.yaml"
        )


class TestSecrets:
    def test_api_key_env_resolves_from_environment_at_use_time(
        self, tmp_path: Path, repo: Path
    ) -> None:
        cfg_path = _global(tmp_path, repo, models={"default": {"api_key_env": "MY_LLM_KEY"}})
        cfg = load_vault_config("work", cli_config=cfg_path, environ={})
        default = cfg.models["default"]
        assert default.api_key_env == "MY_LLM_KEY"
        assert default.resolve_api_key({"MY_LLM_KEY": "secret-value"}) == "secret-value"
        assert default.resolve_api_key({}) is None

    def test_literal_secret_in_config_is_rejected(self, tmp_path: Path, repo: Path) -> None:
        cfg_path = _global(tmp_path, repo, models={"default": {"api_key": "sk-DEADBEEF"}})
        with pytest.raises(ConfigError):
            load_vault_config("work", cli_config=cfg_path, environ={})

    def test_secret_shaped_env_reference_is_rejected(self, tmp_path: Path, repo: Path) -> None:
        cfg_path = _global(
            tmp_path, repo, models={"default": {"api_key_env": "sk-not-an-env-name"}}
        )
        with pytest.raises(ConfigError):
            load_vault_config("work", cli_config=cfg_path, environ={})


class TestMissingFiles:
    def test_missing_per_vault_file_is_fine(self, tmp_path: Path, repo: Path) -> None:
        cfg_path = _global(tmp_path, repo)
        cfg = load_vault_config("work", cli_config=cfg_path, environ={})
        assert cfg.raw_archive is True

    def test_missing_global_file_ok_with_explicit_repo(self, tmp_path: Path, repo: Path) -> None:
        cfg = load_vault_config(
            "work", cli_config=tmp_path / "nope.yaml", environ={}, repo_root=repo
        )
        assert cfg.raw_archive is BUILTIN_DEFAULTS["raw_archive"]

    def test_both_missing_yields_builtin_defaults(self, tmp_path: Path, repo: Path) -> None:
        cfg = load_vault_config(
            "work", cli_config=tmp_path / "nope.yaml", environ={}, repo_root=repo
        )
        assert cfg.auto_push is BUILTIN_DEFAULTS["auto_push"]
        assert cfg.limits.max_tool_calls == BUILTIN_DEFAULTS["limits"]["max_tool_calls"]
