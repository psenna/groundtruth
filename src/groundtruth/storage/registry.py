"""The vault registry (spec §13.1, §13.2).

A persisted ``name -> repo root`` map in the state dir. Registration **adopts** an
existing repo; deregistration removes the entry and the source index but **never
touches files on disk** — the repo belongs to the user.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..errors import GroundtruthError
from ..models import Vault


class RegistryError(GroundtruthError):
    """A registry operation failed."""


class VaultRegistry:
    """Reads and writes ``<state-dir>/registry.json``."""

    def __init__(self, state_dir: Path | str) -> None:
        self._path = Path(state_dir) / "registry.json"

    def _read(self) -> dict[str, str]:
        if not self._path.is_file():
            return {}
        data: dict[str, str] = json.loads(self._path.read_text(encoding="utf-8"))
        return data

    def _write(self, mapping: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.parent / f"{self._path.name}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(mapping, indent=2, sort_keys=True), encoding="utf-8")
        try:
            tmp.replace(self._path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def __contains__(self, name: str) -> bool:
        return name in self._read()

    def get(self, name: str) -> Vault | None:
        root = self._read().get(name)
        return Vault(name=name, repo_root=Path(root)) if root is not None else None

    def list_vaults(self) -> list[Vault]:
        return [Vault(name=n, repo_root=Path(r)) for n, r in sorted(self._read().items())]

    def register(self, name: str, repo_root: Path | str) -> Vault:
        mapping = self._read()
        if name in mapping:
            raise RegistryError(f"vault {name!r} is already registered")
        mapping[name] = str(Path(repo_root))
        self._write(mapping)
        return Vault(name=name, repo_root=Path(repo_root))

    def deregister(self, name: str) -> None:
        mapping = self._read()
        if name not in mapping:
            raise RegistryError(f"vault {name!r} is not registered")
        del mapping[name]
        self._write(mapping)


__all__ = ["RegistryError", "VaultRegistry"]
