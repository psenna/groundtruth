"""Structural checks on the Helm chart and the release pipeline.

These do not run `helm` (CI's Python job has no Helm binary) — `helm lint` and a
rendered-manifest schema check run in the dedicated `helm` CI job. Here we assert
the invariants that matter for groundtruth: non-root, single-writer, state dir
outside the vault volume, and secrets via environment only (spec §5.1, §11.4).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration

_REPO = Path(__file__).parents[2]
_CHART = _REPO / "charts" / "groundtruth"


class TestChartMetadata:
    def test_chart_yaml_is_a_v2_application_chart(self) -> None:
        meta = yaml.safe_load((_CHART / "Chart.yaml").read_text())
        assert meta["apiVersion"] == "v2"
        assert meta["name"] == "groundtruth"
        assert meta["type"] == "application"
        assert "version" in meta and "appVersion" in meta

    def test_default_image_is_the_ghcr_image(self) -> None:
        values = yaml.safe_load((_CHART / "values.yaml").read_text())
        assert values["image"]["repository"] == "ghcr.io/psenna/groundtruth"

    def test_single_replica_by_default(self) -> None:
        values = yaml.safe_load((_CHART / "values.yaml").read_text())
        assert values["replicaCount"] == 1


class TestDeploymentTemplate:
    def _text(self) -> str:
        return (_CHART / "templates" / "deployment.yaml").read_text()

    def test_runs_as_non_root_and_drops_capabilities(self) -> None:
        values = yaml.safe_load((_CHART / "values.yaml").read_text())
        sc = values["securityContext"]
        assert sc["runAsNonRoot"] is True
        assert sc["allowPrivilegeEscalation"] is False
        assert sc["capabilities"]["drop"] == ["ALL"]

    def test_recreate_strategy_for_single_writer_volume(self) -> None:
        assert "type: Recreate" in self._text()

    def test_health_probes_hit_the_unauthenticated_endpoint(self) -> None:
        values = yaml.safe_load((_CHART / "values.yaml").read_text())
        for probe in ("livenessProbe", "readinessProbe"):
            assert values[probe]["httpGet"]["path"] == "/health"
            assert values[probe]["httpGet"]["port"] == "http"

    def test_state_and_vaults_are_separate_mounts(self) -> None:
        text = self._text()
        assert "mountPath: /var/lib/groundtruth" in text
        assert "mountPath: /data" in text

    def test_config_is_mounted_from_the_configmap(self) -> None:
        text = self._text()
        assert "mountPath: /etc/groundtruth/config.yaml" in text
        assert "subPath: config.yaml" in text

    def test_secrets_arrive_only_via_envfrom_secretref(self) -> None:
        text = self._text()
        assert "secretRef:" in text
        # no inline secret values in the pod spec
        for pattern in ("sk-", "ghp_", "API_KEY:", "BEARER_TOKEN:"):
            assert pattern not in text


class TestChartConfigAndSecrets:
    def test_default_config_keeps_state_dir_outside_the_vault_volume(self) -> None:
        values = yaml.safe_load((_CHART / "values.yaml").read_text())
        state_dir = values["config"]["state_dir"]
        for vault_path in values["config"]["vaults"].values():
            assert not vault_path.startswith(state_dir)
        assert state_dir == "/var/lib/groundtruth"

    def test_no_secret_literals_in_values(self) -> None:
        values = yaml.safe_load((_CHART / "values.yaml").read_text())
        assert values["secret"]["data"] in (None, {})
        assert values["secret"]["existingSecret"] == ""

    def test_config_references_api_key_by_env_var_name(self) -> None:
        values = yaml.safe_load((_CHART / "values.yaml").read_text())
        assert values["config"]["defaults"]["models"]["default"]["api_key_env"] == "GT_API_KEY"

    def test_configmap_renders_the_config_block(self) -> None:
        text = (_CHART / "templates" / "configmap.yaml").read_text()
        assert "toYaml .Values.config" in text


class TestReleasePipeline:
    def _release(self) -> str:
        return (_REPO / ".github" / "workflows" / "release.yml").read_text()

    def test_triggers_on_version_tags(self) -> None:
        wf = yaml.safe_load(self._release())
        # PyYAML parses the bare `on:` key as the boolean True.
        trigger = wf[True]
        assert trigger["push"]["tags"] == ["v*.*.*"]

    def test_needs_packages_write_permission(self) -> None:
        wf = yaml.safe_load(self._release())
        assert wf["permissions"]["packages"] == "write"

    def test_builds_and_pushes_the_image(self) -> None:
        text = self._release()
        assert "docker/build-push-action" in text
        assert "push: true" in text

    def test_packages_and_pushes_the_chart_as_oci(self) -> None:
        text = self._release()
        assert "helm package charts/groundtruth" in text
        assert "helm push" in text
        assert "oci://ghcr.io" in text

    def test_ci_lints_the_chart_on_pull_requests(self) -> None:
        ci = (_REPO / ".github" / "workflows" / "ci.yml").read_text()
        assert "helm lint charts/groundtruth" in ci
