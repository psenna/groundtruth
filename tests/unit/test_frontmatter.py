from __future__ import annotations

from datetime import date

import pytest

from groundtruth.models import Note, NoteFrontmatter
from groundtruth.storage.frontmatter import (
    MalformedFrontmatterError,
    MissingFrontmatterError,
    parse_note,
    render_note,
)

SHA_A = "a" * 64
SHA_B = "b" * 64

CANONICAL = f"""\
---
title: Acme Corp
tags: [company, vendor]
sources:
  - {SHA_A}
  - {SHA_B}
created: 2026-03-01
updated: 2026-08-27
---

Acme ships [[Widget Platform]] and was founded in 1996.

Their contract renews annually.
"""


def _note(**fm_overrides: object) -> Note:
    fm = {
        "title": "Acme Corp",
        "tags": ["company", "vendor"],
        "sources": [SHA_A, SHA_B],
        "created": date(2026, 3, 1),
        "updated": date(2026, 8, 27),
    }
    fm.update(fm_overrides)
    return Note(
        path="companies/Acme Corp.md",
        frontmatter=NoteFrontmatter(**fm),  # type: ignore[arg-type]
        body="Body text.\n",
    )


class TestRoundtrip:
    def test_parse_render_parse_is_identical(self) -> None:
        once = parse_note(CANONICAL, path="companies/Acme Corp.md")
        twice = parse_note(render_note(once), path="companies/Acme Corp.md")
        assert once == twice

    def test_render_parse_render_is_byte_identical(self) -> None:
        note = _note()
        first = render_note(note)
        second = render_note(parse_note(first, path=note.path))
        assert first == second

    def test_rendering_is_stable_across_calls(self) -> None:
        note = _note()
        assert render_note(note) == render_note(note)

    def test_key_order_is_fixed(self) -> None:
        rendered = render_note(_note())
        lines = rendered.splitlines()
        close = lines.index("---", 1)
        top_level_keys = [
            ln.split(":", 1)[0] for ln in lines[1:close] if ln and not ln.startswith((" ", "-"))
        ]
        assert top_level_keys == ["title", "tags", "sources", "created", "updated"]


class TestBody:
    def test_horizontal_rule_in_body_does_not_break_parsing(self) -> None:
        text = CANONICAL + "\nSection A\n\n---\n\nSection B\n"
        note = parse_note(text, path="x.md")
        assert "---" in note.body
        assert "Section B" in note.body

    def test_unicode_survives_roundtrip(self) -> None:
        note = _note(title="Café Ω — 日本語")
        note = note.model_copy(update={"body": "Prosa acentuada: ação, münchen, 🚀\n"})
        back = parse_note(render_note(note), path=note.path)
        assert back.frontmatter.title == "Café Ω — 日本語"
        assert "🚀" in back.body

    def test_sources_order_is_preserved(self) -> None:
        note = _note(sources=[SHA_B, SHA_A])
        back = parse_note(render_note(note), path=note.path)
        assert back.frontmatter.sources == [SHA_B, SHA_A]

    def test_tags_render_as_yaml_list(self) -> None:
        assert "tags: [company, vendor]" in render_note(_note())


class TestErrors:
    def test_missing_frontmatter_raises(self) -> None:
        with pytest.raises(MissingFrontmatterError):
            parse_note("Just a body, no frontmatter.\n", path="note.md")

    def test_missing_closing_delimiter_raises(self) -> None:
        with pytest.raises(MissingFrontmatterError):
            parse_note("---\ntitle: X\nbody with no close\n", path="note.md")

    def test_malformed_yaml_names_the_file(self) -> None:
        bad = "---\ntitle: : : :\n  - broken\n---\n\nbody\n"
        with pytest.raises(MalformedFrontmatterError) as excinfo:
            parse_note(bad, path="companies/Broken.md")
        assert "companies/Broken.md" in str(excinfo.value)
