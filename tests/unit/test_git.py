from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from groundtruth.errors import GitConflictError, TerminalError, is_transient
from groundtruth.ingest.commit_message import format_commit_message
from groundtruth.storage.git import (
    GROUNDTRUTH_EMAIL,
    GROUNDTRUTH_NAME,
    GitPushError,
    GitRepo,
)

pytestmark = pytest.mark.integration


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _init_repo(path: Path, *, operator_identity: bool = True) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    if operator_identity:
        _git(path, "config", "user.name", "Operator Sam")
        _git(path, "config", "user.email", "sam@example.com")
    (path / "seed.md").write_text("seed\n")
    _git(path, "add", "-A")
    _git(path, "-c", "user.name=Seed", "-c", "user.email=seed@x", "commit", "-m", "seed")
    return path


@pytest.fixture
def repo(tmp_path: Path) -> GitRepo:
    return GitRepo(_init_repo(tmp_path / "repo"))


class TestIsClean:
    def test_true_on_clean_tree(self, repo: GitRepo) -> None:
        assert repo.is_clean() is True

    def test_false_with_unstaged_change(self, repo: GitRepo) -> None:
        (repo.path / "seed.md").write_text("changed\n")
        assert repo.is_clean() is False

    def test_false_with_staged_change(self, repo: GitRepo) -> None:
        (repo.path / "seed.md").write_text("changed\n")
        _git(repo.path, "add", "-A")
        assert repo.is_clean() is False

    def test_false_with_untracked_file(self, repo: GitRepo) -> None:
        (repo.path / "new.md").write_text("new\n")
        assert repo.is_clean() is False

    def test_missing_path_raises_giterror_not_oserror(self, tmp_path: Path) -> None:
        # A vault deregistered mid-job leaves GitRepo pointed at a path that no
        # longer exists; subprocess can't chdir into it. It must surface as
        # GitError so rollback / restart recovery catch it, not crash startup.
        from groundtruth.storage.git import GitError

        gone = GitRepo(tmp_path / "does-not-exist")
        with pytest.raises(GitError):
            gone.is_clean()


class TestCommit:
    def test_uses_groundtruth_identity_not_operator(self, repo: GitRepo) -> None:
        (repo.path / "note.md").write_text("body\n")
        repo.add()
        sha = repo.commit("ingest(work): a thing")
        who = _git(repo.path, "log", "-1", "--format=%an|%ae|%cn|%ce")
        ident = f"{GROUNDTRUTH_NAME}|{GROUNDTRUTH_EMAIL}"
        assert who == f"{ident}|{ident}"
        assert sha == repo.head_sha()

    def test_global_config_is_not_mutated(self, repo: GitRepo) -> None:
        (repo.path / "note.md").write_text("body\n")
        repo.add()
        repo.commit("ingest(work): x")
        assert _git(repo.path, "config", "user.name") == "Operator Sam"

    def test_message_is_preserved_verbatim(self, repo: GitRepo) -> None:
        msg = format_commit_message(
            vault="work",
            subject="Acme Corp updated",
            created=["Acme Corp"],
            updated=["Vendor Contracts"],
            tags=["company", "vendor"],
            source_sha="a1b2c3d4",
            job_id="01J8XABC",
            excerpt="Acme ships widgets and was founded in 1996.",
        )
        (repo.path / "n.md").write_text("b\n")
        repo.add()
        repo.commit(msg)
        assert _git(repo.path, "log", "-1", "--format=%B").strip() == msg.strip()


class TestRollback:
    def test_removes_modified_and_untracked(self, repo: GitRepo) -> None:
        (repo.path / "seed.md").write_text("tampered\n")
        (repo.path / "extra.md").write_text("junk\n")
        (repo.path / "sub").mkdir()
        (repo.path / "sub" / "deep.md").write_text("junk\n")
        repo.rollback()
        assert repo.is_clean()
        assert (repo.path / "seed.md").read_text() == "seed\n"
        assert not (repo.path / "extra.md").exists()
        assert not (repo.path / "sub").exists()

    def test_clean_repo_is_unchanged(self, repo: GitRepo) -> None:
        before = repo.head_sha()
        repo.rollback()
        assert repo.is_clean()
        assert repo.head_sha() == before


class TestPullFfOnly:
    def _clone(self, origin: Path, dest: Path) -> GitRepo:
        _git(dest.parent, "clone", str(origin), str(dest))
        return GitRepo(dest)

    def test_fast_forward_succeeds(self, tmp_path: Path) -> None:
        origin = _init_repo(tmp_path / "origin")
        clone = self._clone(origin, tmp_path / "clone")
        (origin / "upstream.md").write_text("x\n")
        _git(origin, "add", "-A")
        _git(origin, "-c", "user.name=U", "-c", "user.email=u@x", "commit", "-m", "upstream")
        clone.pull_ff_only()
        assert (clone.path / "upstream.md").exists()

    def test_divergence_raises_terminal(self, tmp_path: Path) -> None:
        origin = _init_repo(tmp_path / "origin")
        clone = self._clone(origin, tmp_path / "clone")
        (origin / "up.md").write_text("x\n")
        _git(origin, "add", "-A")
        _git(origin, "-c", "user.name=U", "-c", "user.email=u@x", "commit", "-m", "up")
        (clone.path / "local.md").write_text("y\n")
        _git(clone.path, "add", "-A")
        _git(clone.path, "-c", "user.name=L", "-c", "user.email=l@x", "commit", "-m", "local")
        with pytest.raises(GitConflictError) as excinfo:
            clone.pull_ff_only()
        assert is_transient(excinfo.value) is False
        assert isinstance(excinfo.value, TerminalError)


class TestPush:
    def test_push_failure_is_distinguishable_and_local_commit_survives(self, repo: GitRepo) -> None:
        _git(repo.path, "remote", "add", "origin", str(repo.path.parent / "does-not-exist"))
        (repo.path / "n.md").write_text("b\n")
        repo.add()
        sha = repo.commit("ingest(work): x")
        with pytest.raises(GitPushError):
            repo.push()
        assert repo.head_sha() == sha
        assert _git(repo.path, "log", "-1", "--format=%s") == "ingest(work): x"
