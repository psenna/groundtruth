from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from groundtruth.api.app import create_app
from groundtruth.api.vaults import build_vaults_router
from groundtruth.auth import build_strategy
from groundtruth.storage.registry import VaultRegistry
from groundtruth.storage.scaffold import STARTER_SCHEMA
from groundtruth.storage.source_index import SourceIndex

pytestmark = pytest.mark.integration


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    state = tmp_path / "state"
    router = build_vaults_router(registry=VaultRegistry(state), source_index=SourceIndex(state))
    return TestClient(create_app(auth=build_strategy("none"), routers=[router]))


def _adoptable_repo(tmp_path: Path, name: str = "work") -> Path:
    repo = tmp_path / f"{name}-repo"
    (repo / name).mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / name / "schema.md").write_text("# Schema\n\n## Folders\n- x/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "seed")
    return repo


class TestAdopt:
    def test_adopts_a_valid_repo(self, client: TestClient, tmp_path: Path) -> None:
        repo = _adoptable_repo(tmp_path)
        resp = client.post("/vaults", json={"name": "work", "repo_root": str(repo)})
        assert resp.status_code == 201
        assert resp.json()["vault_dir"] == str(repo / "work")

    def test_not_a_git_repo_is_422_with_the_problem(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        plain = tmp_path / "plain"
        (plain / "work").mkdir(parents=True)
        resp = client.post("/vaults", json={"name": "work", "repo_root": str(plain)})
        assert resp.status_code == 422
        assert "not a git repository" in resp.json()["detail"]

    def test_missing_vault_dir_is_422(self, client: TestClient, tmp_path: Path) -> None:
        repo = tmp_path / "r"
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        resp = client.post("/vaults", json={"name": "work", "repo_root": str(repo)})
        assert resp.status_code == 422
        assert "vault directory" in resp.json()["detail"]

    def test_missing_schema_is_422(self, client: TestClient, tmp_path: Path) -> None:
        repo = tmp_path / "r"
        (repo / "work").mkdir(parents=True)
        _git(repo, "init", "-b", "main")
        resp = client.post("/vaults", json={"name": "work", "repo_root": str(repo)})
        assert resp.status_code == 422
        assert "schema.md is missing" in resp.json()["detail"]

    def test_dirty_tree_is_422(self, client: TestClient, tmp_path: Path) -> None:
        repo = _adoptable_repo(tmp_path)
        (repo / "work" / "dirty.md").write_text("x\n")
        resp = client.post("/vaults", json={"name": "work", "repo_root": str(repo)})
        assert resp.status_code == 422
        assert "not clean" in resp.json()["detail"]


class TestInit:
    def test_scaffolds_a_new_vault(self, client: TestClient, tmp_path: Path) -> None:
        fresh = tmp_path / "fresh"
        resp = client.post("/vaults", json={"name": "work", "repo_root": str(fresh), "init": True})
        assert resp.status_code == 201
        assert (fresh / ".git").is_dir()
        assert (fresh / "work" / "schema.md").read_text() == STARTER_SCHEMA
        assert (fresh / ".groundtruth.yaml").is_file()
        assert (fresh / ".gitignore").is_file()
        log = subprocess.run(
            ["git", "log", "--oneline"], cwd=fresh, check=True, capture_output=True, text=True
        ).stdout
        assert "scaffold(work)" in log

    def test_init_refuses_a_non_empty_directory(self, client: TestClient, tmp_path: Path) -> None:
        occupied = tmp_path / "occupied"
        occupied.mkdir()
        (occupied / "existing.txt").write_text("keep me\n")
        resp = client.post(
            "/vaults", json={"name": "work", "repo_root": str(occupied), "init": True}
        )
        assert resp.status_code == 422
        assert (occupied / "existing.txt").read_text() == "keep me\n"


class TestListAndDeregister:
    def test_list_returns_registered_vaults(self, client: TestClient, tmp_path: Path) -> None:
        client.post("/vaults", json={"name": "work", "repo_root": str(_adoptable_repo(tmp_path))})
        names = [v["name"] for v in client.get("/vaults").json()]
        assert names == ["work"]

    def test_deregister_leaves_files_on_disk(self, client: TestClient, tmp_path: Path) -> None:
        repo = _adoptable_repo(tmp_path)
        client.post("/vaults", json={"name": "work", "repo_root": str(repo)})
        state = tmp_path / "state"
        SourceIndex(state)._path("work").parent.mkdir(parents=True, exist_ok=True)

        resp = client.delete("/vaults/work")
        assert resp.status_code == 204
        assert client.get("/vaults").json() == []
        assert (repo / "work" / "schema.md").is_file()  # nothing deleted
        assert (repo / ".git").is_dir()

    def test_deregister_unknown_is_404(self, client: TestClient) -> None:
        assert client.delete("/vaults/ghost").status_code == 404
