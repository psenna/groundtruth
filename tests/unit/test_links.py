from __future__ import annotations

from groundtruth.ingest.links import Link, check_links, extract_links

EXISTING = {"companies/Acme Corp.md", "people/Bob.md"}


class TestExtract:
    def test_simple_folder_and_display(self) -> None:
        body = "See [[Simple]] and [[folder/Note]] and [[Note|display text]]."
        links = extract_links(body)
        assert links == [
            Link(target="Simple", display=None),
            Link(target="folder/Note", display=None),
            Link(target="Note", display="display text"),
        ]

    def test_heading_anchor_is_stripped_from_target(self) -> None:
        assert extract_links("[[Note#Section]]") == [Link(target="Note", display=None)]
        anchored = extract_links("[[Note#Section|see here]]")
        assert anchored == [Link(target="Note", display="see here")]

    def test_ignores_fenced_code_blocks(self) -> None:
        body = "real [[Kept]]\n```\ncode [[Ignored]]\n```\nmore [[AlsoKept]]\n"
        assert [link.target for link in extract_links(body)] == ["Kept", "AlsoKept"]

    def test_ignores_inline_code(self) -> None:
        body = "text [[Kept]] and `inline [[Ignored]] code` and [[Kept2]]"
        assert [link.target for link in extract_links(body)] == ["Kept", "Kept2"]

    def test_malformed_brackets_do_not_crash(self) -> None:
        for body in ("[[[weird", "]] [[ ]]", "[[a", "[[]]", "[[ | ]]", "[[a|b|c]]"):
            extract_links(body)  # no exception

    def test_unicode_targets(self) -> None:
        assert extract_links("[[Café Ω 日本語]]") == [Link(target="Café Ω 日本語", display=None)]

    def test_whitespace_around_target_is_trimmed(self) -> None:
        assert extract_links("[[  Spaced Note  ]]") == [Link(target="Spaced Note", display=None)]


class TestCheckLinks:
    def test_link_to_existing_note_by_title_resolves(self) -> None:
        assert check_links(extract_links("[[Acme Corp]]"), EXISTING, set()) == []

    def test_link_to_existing_note_by_path_resolves(self) -> None:
        assert check_links(extract_links("[[companies/Acme Corp]]"), EXISTING, set()) == []

    def test_link_to_note_created_this_job_resolves(self) -> None:
        created = {"companies/Globex.md"}
        assert check_links(extract_links("[[Globex]]"), EXISTING, created) == []

    def test_order_independent(self) -> None:
        # "created this job" is a full set, so a forward reference is fine.
        created = {"a/Later.md"}
        assert check_links(extract_links("[[Later]]"), set(), created) == []

    def test_dangling_link_is_reported(self) -> None:
        links = extract_links("[[Nonexistent]] and [[Acme Corp]]")
        dangling = check_links(links, EXISTING, set())
        assert [d.target for d in dangling] == ["Nonexistent"]

    def test_unicode_note_names_resolve(self) -> None:
        existing = {"notes/Café Ω 日本語.md"}
        assert check_links(extract_links("[[Café Ω 日本語]]"), existing, set()) == []
