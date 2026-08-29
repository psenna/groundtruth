"""Vault registration endpoints (spec §13.1, §13.2, §10.1). Adapter only."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from ..storage.git import GitError, GitRepo
from ..storage.registry import RegistryError, VaultRegistry
from ..storage.scaffold import ScaffoldError, scaffold_vault
from ..storage.source_index import SourceIndex
from .errors import problem


class AdoptRequest(BaseModel):
    name: str
    repo_root: str
    init: bool = False


class VaultInfo(BaseModel):
    name: str
    repo_root: str
    vault_dir: str


def _validate_adopt(repo_root: Path, name: str) -> None:
    if not (repo_root / ".git").is_dir():
        problem(422, f"{repo_root} is not a git repository")
    if not (repo_root / name).is_dir():
        problem(422, f"vault directory {name!r} does not exist under the repo")
    if not (repo_root / name / "schema.md").is_file():
        problem(422, f"{name}/schema.md is missing")
    try:
        if not GitRepo(repo_root).is_clean():
            problem(422, "the working tree is not clean")
    except GitError:
        problem(422, "could not read git status for the repo")


def build_vaults_router(*, registry: VaultRegistry, source_index: SourceIndex) -> APIRouter:
    router = APIRouter()

    @router.post("/vaults", status_code=201, response_model=VaultInfo)
    def adopt(request: AdoptRequest) -> VaultInfo:
        root = Path(request.repo_root)
        if request.name in registry:
            problem(422, f"vault {request.name!r} is already registered")

        if request.init:
            try:
                scaffold_vault(root, request.name)
            except ScaffoldError as exc:
                problem(422, str(exc))
        else:
            _validate_adopt(root, request.name)

        try:
            vault = registry.register(request.name, root)
        except RegistryError as exc:
            problem(422, str(exc))
        return VaultInfo(
            name=vault.name, repo_root=str(vault.repo_root), vault_dir=str(vault.vault_dir)
        )

    @router.get("/vaults", response_model=list[VaultInfo])
    def list_vaults() -> list[VaultInfo]:
        return [
            VaultInfo(name=v.name, repo_root=str(v.repo_root), vault_dir=str(v.vault_dir))
            for v in registry.list_vaults()
        ]

    @router.delete("/vaults/{name}", status_code=204)
    def deregister(name: str) -> None:
        try:
            registry.deregister(name)
        except RegistryError as exc:
            problem(404, str(exc))
        source_index.drop(name)  # remove the index; files on disk are never touched

    return router


__all__ = ["build_vaults_router"]
