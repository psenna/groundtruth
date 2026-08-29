"""Scaffold a new vault repo (spec §13.1, ``init: true``)."""

from __future__ import annotations

from pathlib import Path

from ..errors import GroundtruthError
from .git import GitRepo

#: The starter schema.md — documentation as much as scaffolding (spec §13.1).
STARTER_SCHEMA = """\
# Schema

## Folders
<!-- Describe how notes are organized. The LLM may only create notes in
     folders listed here. -->
- companies/ — organizations
- people/ — individuals
- projects/ — ongoing work

## Tags
<!-- How you want things tagged. This is guidance for the system, not an
     inventory — the tags actually in use are derived from your notes (§5.3),
     so you never have to maintain a list here.
     Lowercase, dash-separated. -->
- Use `vendor` for suppliers, not `supplier`.
- Prefer `project` over `initiative`.
- Tag people with `person` plus their organization.
"""

_STARTER_GROUNDTRUTH_YAML = (
    "# Per-vault overrides (spec §11.3). Same keys as config.yaml 'defaults'.\n"
)
_STARTER_GITIGNORE = "state/\n*.local.yaml\n.env\n"


class ScaffoldError(GroundtruthError):
    """A vault could not be scaffolded."""


def scaffold_vault(repo_root: Path | str, vault_name: str) -> None:
    """Create a fresh vault repo: ``git init``, the §5 layout, and an initial commit.

    Refuses if ``repo_root`` already contains anything.
    """
    root = Path(repo_root)
    if root.exists() and any(root.iterdir()):
        raise ScaffoldError(f"{root} is not empty; refusing to scaffold over existing files")

    (root / vault_name).mkdir(parents=True)
    (root / vault_name / "schema.md").write_text(STARTER_SCHEMA, encoding="utf-8")
    (root / ".groundtruth.yaml").write_text(_STARTER_GROUNDTRUTH_YAML, encoding="utf-8")
    (root / ".gitignore").write_text(_STARTER_GITIGNORE, encoding="utf-8")

    repo = GitRepo(root)
    repo.init()
    repo.add()
    repo.commit(f"scaffold({vault_name}): initial vault layout")


__all__ = ["STARTER_SCHEMA", "ScaffoldError", "scaffold_vault"]
