from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from groundtruth.main import build_app

pytestmark = pytest.mark.integration

_REPO = Path(__file__).parents[2]


def _git(cwd: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    vaults = tmp_path / "data"
    (vaults / "work").mkdir(parents=True)
    _git(vaults / "work", "init", "-b", "main")
    (vaults / "work" / "work").mkdir()
    (vaults / "work" / "work" / "schema.md").write_text("# Schema\n\n## Folders\n- x/\n")
    _git(vaults / "work", "add", "-A")
    _git(vaults / "work", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "server": {"bind": "127.0.0.1", "auth": "none", "mcp_endpoint": "/mcp"},
                "state_dir": str(tmp_path / "state"),
                "vaults": {"work": str(vaults / "work")},
            }
        )
    )
    return cfg


class TestComposedApp:
    def test_api_mcp_and_web_are_all_served(self, config_file: Path) -> None:
        with TestClient(build_app(config_path=config_file, environ={})) as client:
            assert client.get("/health").json() == {"status": "ok"}  # API
            assert client.get("/").status_code == 200  # web query view
            assert client.get("/browse").status_code == 200  # web browse
            assert client.get("/vaults").json()[0]["name"] == "work"  # registry seeded
            # MCP endpoint is mounted and speaks JSON-RPC
            mcp = client.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Accept": "application/json, text/event-stream"},
            )
            assert mcp.status_code in (200, 400)  # mounted (not 404)

    def test_health_needs_no_auth_even_with_bearer(self, tmp_path: Path, config_file: Path) -> None:
        cfg = yaml.safe_load(config_file.read_text())
        cfg["server"] = {"auth": "bearer", "bearer_token_env": "GT_BEARER", "mcp_endpoint": "/mcp"}
        config_file.write_text(yaml.safe_dump(cfg))
        with TestClient(build_app(config_path=config_file, environ={"GT_BEARER": "tok"})) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/vaults").status_code == 401  # protected

    def test_state_dir_is_not_inside_any_vault_repo(self, config_file: Path) -> None:
        cfg = yaml.safe_load(config_file.read_text())
        state = Path(cfg["state_dir"]).resolve()
        for repo_root in cfg["vaults"].values():
            assert not str(state).startswith(str(Path(repo_root).resolve()))


class TestDockerArtifacts:
    def test_dockerfile_runs_as_non_root_and_configures_git_identity(self) -> None:
        text = (_REPO / "Dockerfile").read_text()
        assert "USER groundtruth" in text
        assert "useradd" in text and "--system" in text
        assert 'user.name  "groundtruth"' in text or 'user.name "groundtruth"' in text
        assert "user.email" in text and "groundtruth@localhost" in text
        assert "HEALTHCHECK" in text and "/health" in text

    def test_no_secret_baked_into_any_layer(self) -> None:
        text = (_REPO / "Dockerfile").read_text()
        for pattern in ("sk-", "ghp_", "API_KEY=", "TOKEN=", "PASSWORD="):
            assert pattern not in text

    def test_compose_mounts_state_and_vaults_as_separate_volumes(self) -> None:
        compose = yaml.safe_load((_REPO / "docker-compose.yml").read_text())
        volumes = compose["services"]["groundtruth"]["volumes"]
        mounts = {v.split(":")[1] for v in volumes if ":" in v}
        assert "/var/lib/groundtruth" in mounts  # state dir
        assert "/data" in mounts  # vault repos
        assert "/var/lib/groundtruth" != "/data"

    def test_compose_passes_secrets_only_via_environment(self) -> None:
        compose = yaml.safe_load((_REPO / "docker-compose.yml").read_text())
        env = compose["services"]["groundtruth"]["environment"]
        # values are ${VAR} references, not literals
        for value in env.values():
            assert value.startswith("${") or value == ""
