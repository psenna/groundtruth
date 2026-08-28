from __future__ import annotations

import multiprocessing
from datetime import date
from pathlib import Path

import pytest

from groundtruth.models import SourceRecord
from groundtruth.storage.source_index import SourceIndex

pytestmark = pytest.mark.integration


def _rec(sha_char: str, *, job: str = "01J8X", notes: list[str] | None = None) -> SourceRecord:
    return SourceRecord(
        sha256=sha_char * 64,
        job_id=job,
        commit_sha="deadbeef",
        notes_touched=notes or ["companies/Acme.md"],
        ingested_at=date(2026, 8, 1),
    )


class TestBasics:
    def test_put_then_get(self, tmp_path: Path) -> None:
        idx = SourceIndex(tmp_path)
        rec = _rec("a")
        idx.put("work", rec)
        assert idx.get("work", "a" * 64) == rec

    def test_unknown_hash_returns_none(self, tmp_path: Path) -> None:
        idx = SourceIndex(tmp_path)
        assert idx.get("work", "f" * 64) is None

    def test_index_is_per_vault(self, tmp_path: Path) -> None:
        idx = SourceIndex(tmp_path)
        idx.put("work", _rec("a", job="work-job"))
        idx.put("personal", _rec("a", job="personal-job"))
        assert idx.get("work", "a" * 64).job_id == "work-job"
        assert idx.get("personal", "a" * 64).job_id == "personal-job"

    def test_remove(self, tmp_path: Path) -> None:
        idx = SourceIndex(tmp_path)
        idx.put("work", _rec("a"))
        assert idx.remove("work", "a" * 64) is True
        assert idx.get("work", "a" * 64) is None
        assert idx.remove("work", "a" * 64) is False

    def test_stored_under_state_dir_index(self, tmp_path: Path) -> None:
        idx = SourceIndex(tmp_path)
        idx.put("work", _rec("a"))
        assert (tmp_path / "index" / "work.json").is_file()


class TestAtomicity:
    def test_no_partial_file_on_crash_midwrite(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        idx = SourceIndex(tmp_path)
        idx.put("work", _rec("a"))

        def boom(self: Path, target: Path) -> None:
            raise OSError("simulated crash before rename")

        monkeypatch.setattr(Path, "replace", boom)
        with pytest.raises(OSError, match="simulated crash"):
            idx.put("work", _rec("b"))
        monkeypatch.undo()

        # The old index is intact and parseable; the half-written entry is absent.
        assert idx.get("work", "a" * 64) is not None
        assert idx.get("work", "b" * 64) is None
        assert not list((tmp_path / "index").glob("*.tmp"))


def _worker(args: tuple[str, str, int]) -> None:
    state_dir, vault, start = args
    idx = SourceIndex(state_dir)
    for i in range(start, start + 25):
        sha = f"{i:064x}"
        idx.put(
            vault,
            SourceRecord(
                sha256=sha,
                job_id=f"job-{i}",
                commit_sha="c",
                notes_touched=[],
                ingested_at=date(2026, 8, 1),
            ),
        )


class TestConcurrency:
    def test_concurrent_writers_do_not_lose_entries(self, tmp_path: Path) -> None:
        ctx = multiprocessing.get_context("fork")
        jobs = [(str(tmp_path), "work", 0), (str(tmp_path), "work", 1000)]
        with ctx.Pool(2) as pool:
            pool.map(_worker, jobs)

        idx = SourceIndex(tmp_path)
        for base in (0, 1000):
            for i in range(base, base + 25):
                assert idx.get("work", f"{i:064x}") is not None
