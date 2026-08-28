from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class Vault(BaseModel):
    """A registered vault: a name and the git repo root that contains it (spec §5)."""

    model_config = ConfigDict(extra="forbid")

    #: Registry name; also the vault's directory name inside the repo.
    name: str
    #: Absolute path to the git repo root, one level above the vault directory.
    repo_root: Path

    @property
    def vault_dir(self) -> Path:
        """The directory Obsidian opens: ``<repo_root>/<name>`` (spec §5)."""
        return self.repo_root / self.name
