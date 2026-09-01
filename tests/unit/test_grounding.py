from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from groundtruth.models import AnswerResult, Refusal, Vault
from groundtruth.recovery.grounding import check_grounding

pytestmark = pytest.mark.integration


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    vdir = tmp_path / "repo" / "work"
    (vdir / "companies").mkdir(parents=True)
    (vdir / "schema.md").write_text("# Schema\n\n## Folders\n- companies/\n")
    for name in ("Acme", "Globex"):
        (vdir / "companies" / f"{name}.md").write_text(
            f"---\ntitle: {name}\ntags: [company]\nsources: []\n"
            "created: 2026-01-01\nupdated: 2026-01-01\n---\n\nbody\n"
        )
    return Vault(name="work", repo_root=tmp_path / "repo")


def _answer(text: str) -> AnswerResult:
    return AnswerResult(text=text, citations=[])


class TestGrounding:
    def test_zero_citations_is_a_refusal(self, vault: Vault) -> None:
        result = check_grounding(_answer("Acme was founded in 1996."), vault)
        assert isinstance(result, Refusal)
        assert result.reason == "no_evidence"

    def test_nonexistent_citation_is_a_refusal(self, vault: Vault) -> None:
        result = check_grounding(_answer("Founded 1996. [[companies/Nonexistent]]"), vault)
        assert isinstance(result, Refusal)
        assert result.reason == "no_evidence"

    def test_all_citations_resolve_passes_through(self, vault: Vault) -> None:
        answer = _answer("Acme [[companies/Acme]] rivals Globex [[companies/Globex]].")
        result = check_grounding(answer, vault)
        assert isinstance(result, AnswerResult)
        assert result.text == answer.text
        assert {(c.vault, c.path) for c in result.citations} == {
            ("work", "companies/Acme"),
            ("work", "companies/Globex"),
        }

    def test_refusal_propagates_the_incoming_answers_token_usage(self, vault: Vault) -> None:
        from groundtruth.models import TokenCounts

        usage = {"answer": TokenCounts(prompt_tokens=3, completion_tokens=2, total_tokens=5)}
        answer = AnswerResult(
            text="Acme was founded in 1996.",  # no citation -> refusal
            citations=[],
            token_usage=usage,
        )
        result = check_grounding(answer, vault)
        assert isinstance(result, Refusal)
        assert result.token_usage == usage

    def test_bare_note_name_resolves(self, vault: Vault) -> None:
        assert isinstance(check_grounding(_answer("x [[Acme]]"), vault), AnswerResult)

    def test_partially_valid_answer_is_a_total_refusal(self, vault: Vault) -> None:
        answer = _answer("Real [[companies/Acme]] and fake [[companies/Ghost]].")
        result = check_grounding(answer, vault)
        assert isinstance(result, Refusal)
        # not a filtered/repaired answer
        assert not isinstance(result, AnswerResult)

    def test_citation_validated_against_its_own_vault(self, vault: Vault) -> None:
        # A citation naming a different vault cannot be verified here -> refusal.
        result = check_grounding(_answer("x [[personal:companies/Acme]]"), vault)
        assert isinstance(result, Refusal)


class TestNoBypass:
    def test_check_grounding_has_no_skip_parameter(self) -> None:
        params = inspect.signature(check_grounding).parameters
        assert set(params) == {"answer", "vault"}
        for name in params:
            assert "skip" not in name and "force" not in name and "bypass" not in name

    def test_module_exposes_only_the_gate(self) -> None:
        import groundtruth.recovery.grounding as mod

        public = {n for n in dir(mod) if not n.startswith("_")}
        # only check_grounding plus its imported names; no "lenient"/"soft" variant
        assert not any("lenient" in n or "soft" in n or "partial" in n for n in public)

    def test_citation_extraction_reuses_extract_links(self) -> None:
        source = Path(__file__).parents[2] / "src/groundtruth/recovery/grounding.py"
        assert "from ..ingest.links import" in source.read_text()
        assert "extract_links" in source.read_text()
