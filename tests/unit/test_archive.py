from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from groundtruth.ingest.archive import set_commit_sha, write_archive

SHA = "a" * 64


def _write(repo: Path, *, enabled: bool, text: str = "original source text\n") -> object:
    return write_archive(
        repo,
        sha256=SHA,
        text=text,
        source_label="acme-email.txt",
        job_id="01J8X",
        ingested_at=date(2026, 8, 1),
        notes_touched=["companies/Acme.md"],
        enabled=enabled,
    )


class TestEnabled:
    def test_writes_txt_and_json(self, tmp_path: Path) -> None:
        _write(tmp_path, enabled=True)
        assert (tmp_path / "external" / f"{SHA}.txt").read_text() == "original source text\n"
        assert (tmp_path / "external" / f"{SHA}.json").is_file()

    def test_manifest_fields(self, tmp_path: Path) -> None:
        _write(tmp_path, enabled=True)
        manifest = json.loads((tmp_path / "external" / f"{SHA}.json").read_text())
        assert manifest["hash"] == SHA
        assert manifest["ingested_at"] == "2026-08-01"
        assert manifest["source_label"] == "acme-email.txt"
        assert manifest["job_id"] == "01J8X"
        assert manifest["commit_sha"] is None
        assert manifest["notes_touched"] == ["companies/Acme.md"]

    def test_txt_is_immutable_on_reingest(self, tmp_path: Path) -> None:
        _write(tmp_path, enabled=True, text="first\n")
        _write(tmp_path, enabled=True, text="TAMPERED\n")
        assert (tmp_path / "external" / f"{SHA}.txt").read_text() == "first\n"

    def test_external_is_in_repo_but_outside_vault(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        (repo / "work").mkdir(parents=True)  # the vault
        _write(repo, enabled=True)
        external = repo / "external"
        assert external.is_dir()
        assert external.parent == repo
        assert not str(external).startswith(str(repo / "work"))

    def test_commit_sha_written_after_the_fact(self, tmp_path: Path) -> None:
        _write(tmp_path, enabled=True)
        set_commit_sha(tmp_path, SHA, "deadbeefcafe")
        manifest = json.loads((tmp_path / "external" / f"{SHA}.json").read_text())
        assert manifest["commit_sha"] == "deadbeefcafe"


class TestDisabled:
    def test_writes_nothing(self, tmp_path: Path) -> None:
        result = _write(tmp_path, enabled=False)
        assert result is None
        assert not (tmp_path / "external").exists()
