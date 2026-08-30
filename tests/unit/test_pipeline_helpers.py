from __future__ import annotations

from groundtruth.ingest.pipeline import _note_path_listing, _retry_feedback
from groundtruth.ingest.validator import ValidationRejectionError


class TestRetryFeedback:
    def test_names_the_rule_the_note_and_the_detail(self) -> None:
        exc = ValidationRejectionError(
            "link_integrity", "projects/Acme.md", "dangling wikilink(s): projects/ghost"
        )
        msg = _retry_feedback(exc)
        assert "link_integrity" in msg
        assert "projects/Acme.md" in msg
        assert "projects/ghost" in msg
        assert "nothing was saved" in msg
        # tells the model to redo the whole batch, not patch one note
        assert "ENTIRE batch" in msg

    def test_carries_a_rule_specific_hint(self) -> None:
        collision = _retry_feedback(
            ValidationRejectionError("collision", "a/b.md", "create targets a note that exists")
        )
        assert "update_note" in collision

        link = _retry_feedback(
            ValidationRejectionError("link_integrity", "a/b.md", "dangling wikilink(s): x")
        )
        assert "plain text" in link

    def test_unknown_rule_still_produces_actionable_feedback(self) -> None:
        msg = _retry_feedback(ValidationRejectionError("some_new_rule", "a/b.md", "nope"))
        assert "some_new_rule" in msg
        assert "Redo" in msg


class TestNotePathListing:
    def test_empty_vault_message(self) -> None:
        assert _note_path_listing(set()) == "(the vault has no notes yet)"

    def test_sorted_bulleted_and_capped(self) -> None:
        listing = _note_path_listing({f"f/{i:04d}.md" for i in range(1200)})
        lines = listing.splitlines()
        assert len(lines) == 1000
        assert lines[0] == "- f/0000.md"
        assert lines == sorted(lines)
