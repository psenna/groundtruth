"""Services wires config values into the pieces it constructs per request."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from groundtruth.api import services as services_mod
from groundtruth.api.services import Services
from groundtruth.llm.client import LLMResponse
from groundtruth.storage.job_store import JobStore
from groundtruth.storage.registry import VaultRegistry
from groundtruth.storage.source_index import SourceIndex


class _CaptureClient:
    """Records the kwargs it was built with; answers with an immediate no-op."""

    last_kwargs: ClassVar[dict[str, object]] = {}

    def __init__(self, models: object, **kwargs: object) -> None:
        _CaptureClient.last_kwargs = kwargs

    def complete(self, role: str, messages: object, **kw: object) -> LLMResponse:
        return LLMResponse(role=role, model="m", text="I don't know.", tool_calls=[])


@pytest.fixture
def vault_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "work").mkdir(parents=True)
    (root / "work" / "schema.md").write_text("# Schema\n\n## Folders\n- x/\n")
    return root


def _services(tmp_path: Path, vault_repo: Path, **kw: object) -> Services:
    state = tmp_path / "state"
    registry = VaultRegistry(state)
    registry.register("work", vault_repo)
    return Services(
        state_dir=str(state),
        registry=registry,
        job_store=JobStore(state),
        source_index=SourceIndex(state),
        environ={},
        **kw,
    )


def test_llm_timeout_defaults_to_60s(tmp_path: Path, vault_repo: Path) -> None:
    assert _services(tmp_path, vault_repo).llm_timeout_s == 60.0


def test_query_builds_the_llm_client_with_the_configured_timeout(
    tmp_path: Path, vault_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(services_mod, "LLMClient", _CaptureClient)
    svc = _services(tmp_path, vault_repo, llm_timeout_s=300.0)

    svc.query("work", "anything?")  # returns a refusal; we only care about construction

    assert _CaptureClient.last_kwargs["timeout"] == 300.0
