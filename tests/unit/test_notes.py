from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from groundtruth.models import Note, NoteFrontmatter
from groundtruth.storage.notes import NoteNotFoundError, NoteRepository
from groundtruth.storage.paths import UnsafePathError

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _note(
    path: str,
    *,
    tags: list[str] | None = None,
    sources: list[str] | None = None,
    created: date | None = None,
    body: str = "Body.\n",
) -> Note:
    return Note(
        path=path,
        frontmatter=NoteFrontmatter(
            title=Path(path).stem,
            tags=tags or ["company"],
            sources=sources or [SHA_A],
            created=created or date(2026, 1, 1),
            updated=date(2026, 1, 1),
        ),
        body=body,
    )


@pytest.fixture
def repo(tmp_path: Path) -> NoteRepository:
    root = tmp_path / "work" / "vault"
    root.mkdir(parents=True)
    return NoteRepository(root)


class TestWriteRead:
    def test_write_then_read_is_identical(self, repo: NoteRepository) -> None:
        written = repo.write(_note("companies/Acme Corp.md"))
        assert repo.read("companies/Acme Corp.md") == written

    def test_read_missing_note_raises_named_error(self, repo: NoteRepository) -> None:
        with pytest.raises(NoteNotFoundError) as excinfo:
            repo.read("companies/Nope.md")
        assert "companies/Nope.md" in str(excinfo.value)

    def test_write_outside_vault_raises(self, repo: NoteRepository) -> None:
        with pytest.raises(UnsafePathError):
            repo.write(_note("../escape.md"))

    def test_read_outside_vault_raises(self, repo: NoteRepository) -> None:
        with pytest.raises(UnsafePathError):
            repo.read("../../etc/passwd")


class TestTimestamps:
    def test_updated_is_refreshed_created_preserved(self, repo: NoteRepository) -> None:
        old = date(2020, 5, 5)
        repo.write(_note("n.md", created=old))
        again = repo.write(
            Note(
                path="n.md",
                frontmatter=NoteFrontmatter(
                    title="n",
                    tags=["x"],
                    sources=[SHA_A],
                    created=date(1999, 1, 1),  # caller lies about created
                    updated=date(1999, 1, 1),
                ),
                body="new body\n",
            )
        )
        assert again.frontmatter.created == old  # preserved from disk
        assert again.frontmatter.updated == date.today()
        assert again.frontmatter.updated >= again.frontmatter.created


class TestSources:
    def test_append_does_not_duplicate(self, repo: NoteRepository) -> None:
        repo.write(_note("n.md", sources=[SHA_A, SHA_B]))
        merged = repo.write(_note("n.md", sources=[SHA_B, SHA_C]))
        assert merged.frontmatter.sources == [SHA_A, SHA_B, SHA_C]

    def test_write_dedupes_within_input(self, repo: NoteRepository) -> None:
        written = repo.write(_note("n.md", sources=[SHA_A, SHA_A, SHA_B]))
        assert written.frontmatter.sources == [SHA_A, SHA_B]


class TestListNotes:
    def _seed(self, repo: NoteRepository) -> None:
        repo.write(_note("companies/Acme.md", tags=["company", "vendor"]))
        repo.write(_note("people/Bob.md", tags=["person"]))
        (repo.root / "schema.md").write_text("# Schema\n")
        (repo.root / "notes.txt").write_text("not markdown\n")
        (repo.root / "README").write_text("no extension\n")

    def test_lists_notes_only(self, repo: NoteRepository) -> None:
        self._seed(repo)
        paths = {n.path for n in repo.list_notes()}
        assert paths == {"companies/Acme.md", "people/Bob.md"}

    def test_skips_schema_and_non_markdown(self, repo: NoteRepository) -> None:
        self._seed(repo)
        assert all(n.path != "schema.md" for n in repo.list_notes())

    def test_filters_by_tag(self, repo: NoteRepository) -> None:
        self._seed(repo)
        assert {n.path for n in repo.list_notes(tag="vendor")} == {"companies/Acme.md"}
        assert {n.path for n in repo.list_notes(tag="person")} == {"people/Bob.md"}
        assert repo.list_notes(tag="missing") == []


class TestSymlinkSafety:
    def test_write_refuses_to_follow_a_symlinked_note(
        self, repo: NoteRepository, tmp_path: Path
    ) -> None:
        outside = tmp_path / "outside.md"
        outside.write_text("secret\n")
        (repo.root / "evil.md").symlink_to(outside)
        with pytest.raises(UnsafePathError):
            repo.write(_note("evil.md"))
        assert outside.read_text() == "secret\n"  # untouched

    def test_list_skips_symlinked_dirs(self, repo: NoteRepository, tmp_path: Path) -> None:
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        (outside / "leak.md").write_text(
            "---\ntitle: L\ntags: [x]\nsources: []\n"
            "created: 2026-01-01\nupdated: 2026-01-01\n---\n\nbody\n"
        )
        (repo.root / "linked").symlink_to(outside)
        repo.write(_note("real.md"))
        assert {n.path for n in repo.list_notes()} == {"real.md"}
