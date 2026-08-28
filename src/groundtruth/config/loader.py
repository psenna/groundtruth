from __future__ import annotations

import os
import re
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .defaults import BUILTIN_DEFAULTS
from .schema import ConfigError, VaultConfig

_DEFAULT_GLOBAL_PATH = Path("/etc/groundtruth/config.yaml")

_SECRET_KEY_RE = re.compile(r"(?i)(api[_-]?key|secret|password|passwd|token|credential)$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def resolve_global_config_path(
    *, cli_config: Path | str | None = None, environ: Mapping[str, str] | None = None
) -> Path:
    """Resolve the global config path: ``--config``, then ``$GT_CONFIG``, then default (§11.1)."""
    environ = os.environ if environ is None else environ
    if cli_config is not None:
        return Path(cli_config)
    from_env = environ.get("GT_CONFIG")
    if from_env:
        return Path(from_env)
    return _DEFAULT_GLOBAL_PATH


def _deep_merge(base: Mapping[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    """Per-key recursive merge (spec §11.1): nested maps merge; scalars and lists replace."""
    result: dict[str, Any] = deepcopy(dict(base))
    for key, value in over.items():
        existing = result.get(key)
        if isinstance(value, Mapping) and isinstance(existing, Mapping):
            result[key] = _deep_merge(existing, value)
        else:
            result[key] = deepcopy(value)
    return result


def _scan_for_secrets(node: Any, path: str = "") -> None:
    """Reject a literal secret; only ``*_env`` keys naming an env var are allowed (§11.4)."""
    if isinstance(node, Mapping):
        for raw_key, value in node.items():
            key = str(raw_key)
            full = f"{path}.{key}" if path else key
            if key.endswith("_env"):
                if isinstance(value, str) and not _ENV_NAME_RE.match(value):
                    raise ConfigError(
                        f"{full}: {value!r} is not a valid environment variable name — "
                        "config references secrets by env var name only (§11.4)"
                    )
            elif _SECRET_KEY_RE.search(key):
                raise ConfigError(
                    f"{full}: config must not contain a literal secret; "
                    f"use '{key}_env' naming an environment variable instead (§11.4)"
                )
            else:
                _scan_for_secrets(value, full)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _scan_for_secrets(item, f"{path}[{i}]")


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text())
    if loaded is None:
        return {}
    if not isinstance(loaded, Mapping):
        raise ConfigError(f"{path}: expected a YAML mapping at the top level")
    return dict(loaded)


def _resolve_model_roles(models: Mapping[str, Any]) -> dict[str, Any]:
    """Fill each role's missing keys from ``models.default`` (spec §11.2)."""
    default = dict(models.get("default", {}))
    resolved: dict[str, Any] = {}
    for role, spec in models.items():
        spec_dict = dict(spec) if isinstance(spec, Mapping) else {}
        resolved[role] = spec_dict if role == "default" else {**default, **spec_dict}
    return resolved


def load_vault_config(
    vault: str,
    *,
    cli_config: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
    repo_root: Path | str | None = None,
) -> VaultConfig:
    """Return the fully-resolved, typed config for ``vault`` (spec §11.1).

    Precedence, most specific first: ``<repo>/.groundtruth.yaml`` > global
    ``config.yaml`` ``defaults:`` > built-in defaults. Missing files are fine.
    """
    environ = os.environ if environ is None else environ

    global_path = resolve_global_config_path(cli_config=cli_config, environ=environ)
    global_data = _load_yaml_mapping(global_path)
    _scan_for_secrets(global_data)
    global_defaults = global_data.get("defaults") or {}
    if not isinstance(global_defaults, Mapping):
        raise ConfigError(f"{global_path}: 'defaults' must be a mapping")

    if repo_root is None:
        registry = global_data.get("vaults") or {}
        if not isinstance(registry, Mapping) or vault not in registry:
            raise ConfigError(
                f"vault {vault!r} is not registered in {global_path}; "
                "pass repo_root explicitly if there is no global config"
            )
        repo_root = Path(registry[vault])
    else:
        repo_root = Path(repo_root)

    vault_data = _load_yaml_mapping(repo_root / ".groundtruth.yaml")
    _scan_for_secrets(vault_data)

    merged = _deep_merge(BUILTIN_DEFAULTS, global_defaults)
    merged = _deep_merge(merged, vault_data)
    merged["models"] = _resolve_model_roles(merged.get("models") or {})

    return VaultConfig.model_validate(merged)
