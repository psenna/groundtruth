from __future__ import annotations

from groundtruth.ingest.commit_message import format_commit_message


def _msg(**over: object) -> str:
    kwargs: dict[str, object] = {
        "vault": "work",
        "subject": "Acme Corp",
        "created": ["Acme Corp", "Widget Platform"],
        "updated": ["Vendor Contracts"],
        "tags": ["company", "vendor"],
        "source_sha": "a1b2c3d4e5f6",
        "job_id": "01J8X",
        "excerpt": "Acme ships widgets and was founded in 1996.",
    }
    kwargs.update(over)
    return format_commit_message(**kwargs)  # type: ignore[arg-type]


def test_matches_spec_7_9_format() -> None:
    msg = _msg()
    lines = msg.splitlines()
    assert lines[0] == "ingest(work): Acme Corp"
    assert lines[1] == ""
    assert "notes:   created Acme Corp, Widget Platform · updated Vendor Contracts" in msg
    assert "tags:    company, vendor" in msg
    assert "source:  sha256:a1b2c3d4e5f6" in msg
    assert "job:     01J8X" in msg
    assert msg.endswith("founded in 1996.\n")


def test_notes_line_when_only_creates() -> None:
    assert "notes:   created Acme Corp" in _msg(updated=[])


def test_notes_line_when_nothing_touched() -> None:
    assert "notes:   none" in _msg(created=[], updated=[])


def test_is_pure() -> None:
    assert _msg() == _msg()


def test_excerpt_is_truncated() -> None:
    msg = _msg(excerpt="word " * 200)
    excerpt_line = msg.rsplit("\n\n", 1)[1]
    assert len(excerpt_line) <= 205


def test_excerpt_contains_no_secrets() -> None:
    leaky = "The API key is sk-ABCDEF0123456789ABCDEF and also Bearer aaaaaaaaaaaaaaaaaaaaaaaa done"
    msg = _msg(excerpt=leaky)
    assert "sk-ABCDEF0123456789ABCDEF" not in msg
    assert "Bearer aaaaaaaaaaaaaaaaaaaaaaaa" not in msg
    assert "[redacted]" in msg
