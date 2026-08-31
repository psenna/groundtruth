"""Git operations for the ingest pipeline (spec §7.1, §7.3, §7.7, §7.9, §7.10).

The clean-tree check and the rollback are a pair (ADR-4, invariants 5 and 7):
``rollback()`` runs ``git reset --hard`` + ``git clean -fd``, which is only safe
because ``is_clean()`` verified the tree at job start. Commits use a dedicated
``groundtruth`` identity passed per invocation — the operator's git config is
never mutated.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..errors import GitConflictError, GroundtruthError, TransientError

GROUNDTRUTH_NAME = "groundtruth"
GROUNDTRUTH_EMAIL = "groundtruth@localhost"

_IDENTITY_ARGS = (
    "-c",
    f"user.name={GROUNDTRUTH_NAME}",
    "-c",
    f"user.email={GROUNDTRUTH_EMAIL}",
    "-c",
    "commit.gpgsign=false",
)

_NETWORK_MARKERS = (
    "could not resolve host",
    "connection refused",
    "connection timed out",
    "unable to access",
    "could not read from remote",
    "network is unreachable",
    "operation timed out",
)


class GitError(GroundtruthError):
    """A git command failed."""


class GitNetworkError(TransientError):
    """A git network operation failed in a way that may succeed on retry."""


class GitPushError(GitError):
    """``push()`` failed. The local commit is still valid — only the sync did not happen (§7.10)."""


class GitRepo:
    """A wrapper around one git working tree."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.path,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            # cwd does not exist (e.g. a vault deregistered while a job was
            # in flight), or `git` is not on PATH. Surface it as a GitError so
            # callers that already handle GitError — rollback, restart recovery
            # (§4.4) — do not crash on it.
            raise GitError(f"git {' '.join(args)} could not start in {self.path}: {exc}") from exc
        if check and result.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
            )
        return result

    def init(self, *, branch: str = "main") -> None:
        """``git init`` a fresh repo at this path."""
        self.path.mkdir(parents=True, exist_ok=True)
        self._run("init", "-b", branch)

    def is_clean(self) -> bool:
        """True iff there are no staged, unstaged or untracked changes (spec §7.1)."""
        return self._run("status", "--porcelain").stdout.strip() == ""

    def head_sha(self) -> str:
        return self._run("rev-parse", "HEAD").stdout.strip()

    def add(self, *paths: str) -> None:
        if paths:
            self._run("add", "--", *paths)
        else:
            self._run("add", "-A")

    def commit(self, message: str) -> str:
        """Commit staged changes with the ``groundtruth`` identity; return the new sha."""
        self._run(*_IDENTITY_ARGS, "commit", "-m", message)
        return self.head_sha()

    def commit_paths(self, paths: list[str], message: str) -> str:
        """Commit only ``paths``, ignoring anything else staged or dirty. Returns the sha."""
        self._run(*_IDENTITY_ARGS, "commit", "-m", message, "--", *paths)
        return self.head_sha()

    def amend(self) -> str:
        """Fold currently-staged changes into HEAD, keeping its message. Returns the new sha."""
        self._run(*_IDENTITY_ARGS, "commit", "--amend", "--no-edit")
        return self.head_sha()

    def rollback(self) -> None:
        """Discard every change since HEAD (spec §7.7). Safe only after ``is_clean()``."""
        self._run("reset", "--hard", "HEAD")
        self._run("clean", "-fd")

    def pull_ff_only(self) -> None:
        """``git pull --ff-only`` (spec §7.3). Divergence is terminal; network is transient."""
        result = self._run("pull", "--ff-only", check=False)
        if result.returncode == 0:
            return
        err = result.stderr.lower()
        if any(marker in err for marker in _NETWORK_MARKERS):
            raise GitNetworkError(
                f"pull --ff-only network failure: {result.stderr.strip()}", stage="pre-sync"
            )
        raise GitConflictError(
            f"pull --ff-only could not fast-forward: {result.stderr.strip()}", stage="pre-sync"
        )

    def push(self, *args: str) -> None:
        """``git push`` (spec §7.10). Any failure raises ``GitPushError``; HEAD is unaffected."""
        result = self._run("push", *args, check=False)
        if result.returncode != 0:
            raise GitPushError(
                f"push failed (local commit {self.head_sha()} is intact): {result.stderr.strip()}",
                stage="push",
            )


__all__ = [
    "GROUNDTRUTH_EMAIL",
    "GROUNDTRUTH_NAME",
    "GitError",
    "GitNetworkError",
    "GitPushError",
    "GitRepo",
]
