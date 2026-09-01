from __future__ import annotations

from groundtruth.ingest.links import Link, check_links, downgrade_links, extract_links

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


class TestDowngradeLinks:
    def test_bare_link_becomes_its_target_text(self) -> None:
        body, downgraded = downgrade_links("See [[people/Nobody]] now.", {"people/Nobody"})
        assert body == "See people/Nobody now."
        assert downgraded == ["people/Nobody"]

    def test_aliased_link_becomes_its_alias(self) -> None:
        body, downgraded = downgrade_links("By [[people/Nobody|the founder]].", {"people/Nobody"})
        assert body == "By the founder."
        assert downgraded == ["people/Nobody"]

    def test_only_targeted_links_are_touched(self) -> None:
        body, downgraded = downgrade_links(
            "[[people/Bob]] met [[people/Nobody]].", {"people/Nobody"}
        )
        assert body == "[[people/Bob]] met people/Nobody."
        assert downgraded == ["people/Nobody"]

    def test_repeated_target_is_reported_once_and_rewritten_everywhere(self) -> None:
        body, downgraded = downgrade_links("[[x/Y]] and again [[x/Y|alias]].", {"x/Y"})
        assert body == "x/Y and again alias."
        assert downgraded == ["x/Y"]

    def test_links_inside_code_are_left_alone(self) -> None:
        src = "Inline `[[x/Y]]` and\n\n```\n[[x/Y]]\n```\n\nprose [[x/Y]]."
        body, downgraded = downgrade_links(src, {"x/Y"})
        assert body == "Inline `[[x/Y]]` and\n\n```\n[[x/Y]]\n```\n\nprose x/Y."
        assert downgraded == ["x/Y"]

    def test_no_targets_is_a_no_op(self) -> None:
        body, downgraded = downgrade_links("[[x/Y]]", set())
        assert body == "[[x/Y]]"
        assert downgraded == []
