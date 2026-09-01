from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from groundtruth.models import (
    AnswerResult,
    Citation,
    JobRecord,
    JobState,
    Note,
    NoteFrontmatter,
    Refusal,
    SourceRecord,
    TokenCounts,
    Vault,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


def _frontmatter(**overrides: object) -> NoteFrontmatter:
    kwargs: dict[str, object] = {
        "title": "Acme Corp",
        "tags": ["company", "vendor"],
        "sources": [SHA_A],
        "created": date(2026, 3, 1),
        "updated": date(2026, 8, 27),
    }
    kwargs.update(overrides)
    return NoteFrontmatter(**kwargs)  # type: ignore[arg-type]


class TestNoteFrontmatter:
    def test_valid(self) -> None:
        fm = _frontmatter()
        assert fm.title == "Acme Corp"
        assert fm.tags == ["company", "vendor"]
        assert fm.sources == [SHA_A]

    @pytest.mark.parametrize("missing", ["title", "tags", "sources", "created", "updated"])
    def test_required_fields(self, missing: str) -> None:
        kwargs = {
            "title": "Acme Corp",
            "tags": ["company"],
            "sources": [SHA_A],
            "created": date(2026, 3, 1),
            "updated": date(2026, 8, 27),
        }
        del kwargs[missing]
        with pytest.raises(ValidationError):
            NoteFrontmatter(**kwargs)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", ["Company", "two words", "under_score", "trailing-", "-lead"])
    def test_tag_rejects_non_normalized(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            _frontmatter(tags=[bad])

    @pytest.mark.parametrize("ok", ["company", "vendor", "b2b", "multi-word-tag"])
    def test_tag_accepts_normalized(self, ok: str) -> None:
        assert _frontmatter(tags=[ok]).tags == [ok]

    def test_sources_accepts_sha256_hex(self) -> None:
        assert _frontmatter(sources=[SHA_A, SHA_B]).sources == [SHA_A, SHA_B]

    @pytest.mark.parametrize(
        "bad",
        [
            "a" * 63,  # too short
            "a" * 65,  # too long
            "g" * 64,  # not hex
            "A" * 64,  # uppercase
            "not-a-hash",
        ],
    )
    def test_sources_rejects_non_sha256(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            _frontmatter(sources=[bad])


class TestNote:
    def test_carries_frontmatter_path_and_body(self) -> None:
        note = Note(
            path="companies/Acme Corp.md",
            frontmatter=_frontmatter(),
            body="Acme ships things.",
        )
        assert note.path == "companies/Acme Corp.md"
        assert note.frontmatter.title == "Acme Corp"
        assert note.body == "Acme ships things."


class TestVault:
    def test_valid(self) -> None:
        v = Vault(name="work", repo_root="/data/work-repo")
        assert v.name == "work"
        assert str(v.repo_root) == "/data/work-repo"

    def test_vault_dir_is_below_repo_root(self) -> None:
        v = Vault(name="work", repo_root="/data/work-repo")
        assert str(v.vault_dir) == "/data/work-repo/work"


class TestSourceRecord:
    def test_valid(self) -> None:
        rec = SourceRecord(
            sha256=SHA_A,
            job_id="01J8X",
            commit_sha="deadbeef",
            notes_touched=["companies/Acme Corp.md"],
            ingested_at=date(2026, 8, 27),
        )
        assert rec.sha256 == SHA_A
        assert rec.notes_touched == ["companies/Acme Corp.md"]

    def test_rejects_bad_sha(self) -> None:
        with pytest.raises(ValidationError):
            SourceRecord(
                sha256="nope",
                job_id="01J8X",
                commit_sha="deadbeef",
                notes_touched=[],
                ingested_at=date(2026, 8, 27),
            )


class TestJobRecord:
    def test_starts_queued(self) -> None:
        job = JobRecord(id="01J8X", vault="work")
        assert job.state is JobState.QUEUED

    def test_link_downgrades_defaults_empty_and_round_trips(self) -> None:
        job = JobRecord(id="01J8X", vault="work")
        assert job.link_downgrades == {}
        loaded = JobRecord.model_validate_json(
            job.model_copy(
                update={"link_downgrades": {"companies/Acme.md": ["people/Nobody"]}}
            ).model_dump_json()
        )
        assert loaded.link_downgrades == {"companies/Acme.md": ["people/Nobody"]}

    def test_legal_transition(self) -> None:
        job = JobRecord(id="01J8X", vault="work")
        running = job.transitioned_to(JobState.RUNNING)
        assert running.state is JobState.RUNNING
        assert running.transitioned_to(JobState.SUCCEEDED).state is JobState.SUCCEEDED

    @pytest.mark.parametrize(
        ("frm", "to"),
        [
            (JobState.QUEUED, JobState.SUCCEEDED),
            (JobState.SUCCEEDED, JobState.RUNNING),
            (JobState.FAILED, JobState.RUNNING),
            (JobState.RUNNING, JobState.QUEUED),
        ],
    )
    def test_illegal_transition_rejected(self, frm: JobState, to: JobState) -> None:
        job = JobRecord(id="01J8X", vault="work", state=frm)
        with pytest.raises(ValueError):
            job.transitioned_to(to)


class TestTokenCounts:
    def test_defaults_to_all_zero(self) -> None:
        tc = TokenCounts()
        assert (tc.prompt_tokens, tc.completion_tokens, tc.total_tokens) == (0, 0, 0)

    def test_add_is_field_wise(self) -> None:
        a = TokenCounts(prompt_tokens=1, completion_tokens=2, total_tokens=3)
        b = TokenCounts(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        assert a + b == TokenCounts(prompt_tokens=11, completion_tokens=22, total_tokens=33)

    def test_from_usage_reads_the_three_fields(self) -> None:
        from groundtruth.llm.client import TokenUsage

        tc = TokenCounts.from_usage(TokenUsage(3, 2, 5))
        assert tc == TokenCounts(prompt_tokens=3, completion_tokens=2, total_tokens=5)

    def test_is_frozen_and_forbids_extra(self) -> None:
        with pytest.raises(ValidationError):
            TokenCounts(prompt_tokens=1, unexpected=2)  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            TokenCounts().prompt_tokens = 9  # type: ignore[misc]

    def test_job_record_widens_a_legacy_bare_int(self) -> None:
        # Records persisted before #116 stored dict[str, int]; they must still load.
        job = JobRecord.model_validate(
            {"id": "j", "vault": "work", "token_usage": {"reduce": 42, "tag": 7}}
        )
        assert job.token_usage["reduce"] == TokenCounts(total_tokens=42)
        assert job.token_usage["tag"].total_tokens == 7

    def test_job_record_keeps_a_dict_value_untouched(self) -> None:
        job = JobRecord.model_validate(
            {
                "id": "j",
                "vault": "work",
                "token_usage": {"answer": {"prompt_tokens": 1, "completion_tokens": 2}},
            }
        )
        assert job.token_usage["answer"] == TokenCounts(prompt_tokens=1, completion_tokens=2)


class TestCitation:
    def test_is_structured_not_a_string(self) -> None:
        c = Citation(vault="work", path="companies/Acme Corp.md")
        assert c.vault == "work"
        assert c.path == "companies/Acme Corp.md"
        assert not isinstance(c, str)

    def test_requires_both_fields(self) -> None:
        with pytest.raises(ValidationError):
            Citation(path="companies/Acme Corp.md")  # type: ignore[call-arg]


class TestAnswerAndRefusal:
    def test_answer_and_refusal_are_distinct_types(self) -> None:
        answer = AnswerResult(
            text="Acme was founded in 1996.",
            citations=[Citation(vault="work", path="a.md")],
        )
        refusal = Refusal(reason="no_evidence")
        assert isinstance(answer, AnswerResult)
        assert isinstance(refusal, Refusal)
        assert not isinstance(refusal, AnswerResult)
        assert not isinstance(answer, Refusal)

    @pytest.mark.parametrize("reason", ["no_evidence", "budget_exhausted"])
    def test_refusal_reasons(self, reason: str) -> None:
        assert Refusal(reason=reason).reason == reason  # type: ignore[arg-type]

    def test_refusal_rejects_other_reasons(self) -> None:
        with pytest.raises(ValidationError):
            Refusal(reason="just_because")  # type: ignore[arg-type]
