from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from groundtruth.config import Limits
from groundtruth.errors import TerminalError, is_transient
from groundtruth.ingest.schema import parse_schema
from groundtruth.ingest.validator import ValidationRejectionError, validate
from groundtruth.ingest.write_tools import PendingNote, PendingWrites
from groundtruth.models import NoteFrontmatter

SHA_A = "a" * 64

SCHEMA = parse_schema("# Schema\n\n## Folders\n- companies/\n- people/\n")

LIMITS = Limits(
    max_notes_per_ingest=3,
    max_note_bytes=400,
    max_tool_calls=30,
    max_wall_clock_s=60,
    grep_max_matches=50,
    grep_max_bytes=65536,
    read_max_bytes=32768,
    vocab_max_bytes=4096,
)


def _fm(**over: object) -> NoteFrontmatter:
    base: dict[str, object] = {
        "title": "Acme",
        "tags": ["company"],
        "sources": [SHA_A],
        "created": date(2026, 1, 1),
        "updated": date(2026, 1, 1),
    }
    base.update(over)
    return NoteFrontmatter(**base)  # type: ignore[arg-type]


def _note(
    path: str = "companies/Acme.md",
    *,
    body: object = "Acme ships widgets.\n",
    is_new: bool = True,
    folder: str | None = "companies",
    title: str | None = "Acme",
    frontmatter: object = "USE_DEFAULT",
) -> PendingNote:
    return PendingNote(
        path=path,
        body=body,  # type: ignore[arg-type]
        is_new=is_new,
        folder=folder,
        title=title,
        frontmatter=_fm() if frontmatter == "USE_DEFAULT" else frontmatter,
    )


def _pending(*notes: PendingNote) -> PendingWrites:
    return PendingWrites(notes=list(notes))


def _validate(pending: PendingWrites, *, root: Path, existing: set[str] | None = None) -> None:
    validate(pending, SCHEMA, LIMITS, vault_root=str(root), existing_paths=existing or set())


def _expect(
    pending: PendingWrites, rule: str, *, root: Path, existing: set[str] | None = None
) -> None:
    with pytest.raises(ValidationRejectionError) as excinfo:
        _validate(pending, root=root, existing=existing)
    assert excinfo.value.rule == rule


class TestValidBatch:
    def test_valid_create_and_update(self, tmp_path: Path) -> None:
        create = _note()
        update = _note("people/Bob.md", folder=None, title=None, is_new=False)
        _validate(_pending(create, update), root=tmp_path, existing={"people/Bob.md"})

    def test_wikilink_to_a_sibling_created_note(self, tmp_path: Path) -> None:
        a = _note("companies/Acme.md", body="See [[Globex]].\n", title="Acme")
        b = _note("companies/Globex.md", title="Globex")
        _validate(_pending(a, b), root=tmp_path)


class TestSevenSixTable:
    def test_folder_not_in_schema(self, tmp_path: Path) -> None:
        _expect(
            _pending(_note("secret/Leak.md", folder="secret", title="Leak")),
            "folder",
            root=tmp_path,
        )

    def test_undeclared_subfolder(self, tmp_path: Path) -> None:
        bad = _note("companies/sub/x.md", folder="companies/sub", title="x")
        _expect(_pending(bad), "folder", root=tmp_path)

    def test_unsafe_title(self, tmp_path: Path) -> None:
        _expect(_pending(_note("companies/x.md", title="../escape")), "filename", root=tmp_path)

    def test_path_does_not_match_folder_and_title(self, tmp_path: Path) -> None:
        # folder/title say companies/Acme.md; path says people/Acme.md
        mismatch = _note("people/Acme.md", folder="companies", title="Acme")
        _expect(_pending(mismatch), "filename", root=tmp_path)

    def test_path_outside_vault(self, tmp_path: Path) -> None:
        bad = _note("../outside.md", folder=None, title=None, is_new=False)
        _expect(_pending(bad), "containment", root=tmp_path, existing={"../outside.md"})

    def test_too_many_notes(self, tmp_path: Path) -> None:
        many = [_note(f"companies/N{i}.md", title=f"N{i}") for i in range(4)]
        _expect(_pending(*many), "note_count", root=tmp_path)

    def test_oversize_rendered_note(self, tmp_path: Path) -> None:
        big = _note(body="x" * 500 + "\n")
        _expect(_pending(big), "note_size", root=tmp_path)

    def test_note_size_counts_frontmatter_not_just_body(self, tmp_path: Path) -> None:
        # small body, but 40 source hashes push the rendered note past the limit
        fat = _note(body="tiny\n", frontmatter=_fm(sources=[f"{i:064x}" for i in range(40)]))
        _expect(_pending(fat), "note_size", root=tmp_path)

    def test_missing_frontmatter(self, tmp_path: Path) -> None:
        _expect(_pending(_note(frontmatter=None)), "frontmatter", root=tmp_path)

    def test_frontmatter_missing_required_key(self, tmp_path: Path) -> None:
        _expect(
            _pending(_note(frontmatter={"title": "Acme", "tags": ["company"]})),
            "frontmatter",
            root=tmp_path,
        )

    def test_frontmatter_instance_is_revalidated(self, tmp_path: Path) -> None:
        # model_construct bypasses pydantic; the validator must not trust the type
        forged = NoteFrontmatter.model_construct(
            title="Acme",
            tags="not-a-list",
            sources=[SHA_A],
            created=date(2026, 1, 1),
            updated=date(2026, 1, 1),
        )
        _expect(_pending(_note(frontmatter=forged)), "frontmatter", root=tmp_path)

    def test_dangling_wikilink(self, tmp_path: Path) -> None:
        _expect(_pending(_note(body="See [[Nonexistent]].\n")), "link_integrity", root=tmp_path)


class TestInvariantOne:
    def test_schema_md_write_is_rejected(self, tmp_path: Path) -> None:
        bad = _note("schema.md", folder=None, title=None, is_new=False)
        _expect(_pending(bad), "path", root=tmp_path, existing={"schema.md"})

    def test_external_archive_write_is_rejected(self, tmp_path: Path) -> None:
        bad = _note("external/deadbeef.md", folder=None, title=None, is_new=False)
        _expect(_pending(bad), "path", root=tmp_path, existing={"external/deadbeef.md"})

    def test_non_markdown_path_is_rejected(self, tmp_path: Path) -> None:
        bad = _note("companies/Acme.txt", folder=None, title=None, is_new=False)
        _expect(_pending(bad), "path", root=tmp_path, existing={"companies/Acme.txt"})


class TestDefensive:
    def test_non_string_body_is_rejected_not_crashed(self, tmp_path: Path) -> None:
        _expect(_pending(_note(body=None)), "body_type", root=tmp_path)
        _expect(_pending(_note(body={"k": "v"})), "body_type", root=tmp_path)

    def test_duplicate_path_in_batch(self, tmp_path: Path) -> None:
        a = _note("companies/Acme.md")
        b = _note("companies/Acme.md")
        _expect(_pending(a, b), "duplicate_path", root=tmp_path)

    def test_empty_batch_is_rejected(self, tmp_path: Path) -> None:
        _expect(_pending(), "empty_batch", root=tmp_path)

    def test_create_over_existing_note_is_rejected(self, tmp_path: Path) -> None:
        _expect(_pending(_note()), "collision", root=tmp_path, existing={"companies/Acme.md"})

    def test_update_to_nonexistent_note_is_rejected(self, tmp_path: Path) -> None:
        upd = _note("people/Ghost.md", folder=None, title=None, is_new=False)
        _expect(_pending(upd), "missing_target", root=tmp_path)

    def test_overlong_filename_stem_is_rejected(self, tmp_path: Path) -> None:
        bad = _note("companies/" + "x" * 250 + ".md", folder=None, title=None, is_new=False)
        _expect(
            _pending(bad), "filename", root=tmp_path, existing={"companies/" + "x" * 250 + ".md"}
        )


class TestAtomicity:
    def test_partial_validity_rejects_whole_batch(self, tmp_path: Path) -> None:
        good = _note("companies/Good.md", title="Good")
        bad = _note("nope/Bad.md", folder="nope", title="Bad")
        with pytest.raises(TerminalError):
            _validate(_pending(good, bad), root=tmp_path)

    def test_rejection_is_terminal(self, tmp_path: Path) -> None:
        try:
            _validate(_pending(_note("nope/Bad.md", folder="nope", title="Bad")), root=tmp_path)
        except ValidationRejectionError as exc:
            assert isinstance(exc, TerminalError)
            assert is_transient(exc) is False
        else:  # pragma: no cover
            pytest.fail("expected a rejection")


def test_extract_links_is_not_quadratic_on_unterminated_fence() -> None:
    import time

    from groundtruth.ingest.links import extract_links

    body = "```x\n" * 20000  # 100 KB of unterminated fence
    start = time.monotonic()
    extract_links(body)
    assert time.monotonic() - start < 1.0
