from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from groundtruth.ingest.vocabulary import derive_vocabulary
from groundtruth.models import Vault

pytestmark = pytest.mark.integration


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _note(tags: list[str], *, title: str = "N") -> str:
    tag_list = ", ".join(tags)
    return (
        f"---\ntitle: {title}\ntags: [{tag_list}]\nsources: []\n"
        f"created: 2026-01-01\nupdated: 2026-01-01\n---\n\nbody\n"
    )


@pytest.fixture
def vault(tmp_path: Path) -> tuple[Vault, Path]:
    repo = tmp_path / "repo"
    vdir = repo / "work"
    vdir.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (vdir / "schema.md").write_text("# Schema\n\n## Folders\n- x/\n")
    (vdir / "a.md").write_text(_note(["company", "vendor", "company"], title="A"))
    (vdir / "b.md").write_text(_note(["company", "person"], title="B"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "seed")
    state = tmp_path / "state"
    return Vault(name="work", repo_root=repo), state


class TestDerive:
    def test_computed_from_frontmatter_ranked_by_frequency(self, vault: tuple[Vault, Path]) -> None:
        v, state = vault
        vocab = derive_vocabulary(v, state_dir=state)
        assert [(t.tag, t.count) for t in vocab.tags] == [
            ("company", 3),
            ("person", 1),
            ("vendor", 1),
        ]

    def test_small_vault_is_untruncated(self, vault: tuple[Vault, Path]) -> None:
        v, state = vault
        vocab = derive_vocabulary(v, state_dir=state)
        assert vocab.truncated is False
        assert vocab.omitted == 0

    def test_byte_cap_truncates_with_a_message(self, vault: tuple[Vault, Path]) -> None:
        v, state = vault
        vocab = derive_vocabulary(v, state_dir=state, vocab_max_bytes=12)
        assert vocab.truncated is True
        assert vocab.omitted > 0
        assert "omitted" in vocab.render()

    def test_empty_vault_yields_empty_vocabulary(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        vdir = repo / "work"
        vdir.mkdir(parents=True)
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (vdir / "schema.md").write_text("# Schema\n\n## Folders\n- x/\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "seed")
        vocab = derive_vocabulary(Vault(name="work", repo_root=repo), state_dir=tmp_path / "state")
        assert vocab.tags == []

    def test_malformed_frontmatter_is_skipped_with_a_warning(
        self, vault: tuple[Vault, Path]
    ) -> None:
        v, state = vault
        (v.vault_dir / "broken.md").write_text("---\nnot: [valid\n---\n\nbody\n")
        _git(v.repo_root, "add", "-A")
        _git(v.repo_root, "commit", "-m", "add broken")
        with pytest.warns(UserWarning, match="broken.md"):
            vocab = derive_vocabulary(v, state_dir=state)
        assert any(t.tag == "company" for t in vocab.tags)


class TestCache:
    def test_same_head_uses_cache(self, vault: tuple[Vault, Path]) -> None:
        v, state = vault
        first = derive_vocabulary(v, state_dir=state)
        second = derive_vocabulary(v, state_dir=state)
        assert first.from_cache is False
        assert second.from_cache is True

    def test_new_commit_invalidates_cache(self, vault: tuple[Vault, Path]) -> None:
        v, state = vault
        derive_vocabulary(v, state_dir=state)
        (v.vault_dir / "c.md").write_text(_note(["newtag"], title="C"))
        _git(v.repo_root, "add", "-A")
        _git(v.repo_root, "commit", "-m", "more")
        again = derive_vocabulary(v, state_dir=state)
        assert again.from_cache is False
        assert any(t.tag == "newtag" for t in again.tags)

    def test_cache_lives_in_state_dir_not_vault(self, vault: tuple[Vault, Path]) -> None:
        v, state = vault
        derive_vocabulary(v, state_dir=state)
        assert list(state.rglob("*.json"))
        assert not list(v.vault_dir.rglob("*vocab*"))
