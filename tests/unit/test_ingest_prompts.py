from __future__ import annotations

import pytest

from groundtruth.errors import TerminalError
from groundtruth.ingest.prompts import (
    ORGANIZE,
    REDUCE,
    ROLES,
    TAG,
    load_template,
    parse_reduced_items,
    parse_tags,
    render_prompt,
)
from groundtruth.ingest.write_tools import WriteTools
from groundtruth.llm.client import LLMResponse, ToolCall
from groundtruth.retrieval.agent import AgentStatus, run_agent
from groundtruth.retrieval.budget import Budget, BudgetLimits

SCHEMA_MD = "# Schema\n\n## Folders\n- companies/\n\n## Tags\n- Use `vendor`, not `supplier`.\n"
VOCAB = "company (12)\nvendor (5)\nperson (3)"


class TestTemplates:
    def test_three_roles_are_files(self) -> None:
        assert ROLES == (TAG, REDUCE, ORGANIZE)
        for role in ROLES:
            assert load_template(role).strip()

    def test_prompt_carries_schema_verbatim_and_derived_vocabulary(self) -> None:
        for role in (TAG, ORGANIZE):
            rendered = render_prompt(
                role,
                schema_md=SCHEMA_MD,
                derived_vocabulary=VOCAB,
                input_text="x",
                input_items="x",
                existing_notes="none",
                existing_note_paths="- projects/acme.md",
            )
            assert SCHEMA_MD in rendered  # verbatim, prescriptive
            assert VOCAB in rendered  # descriptive, derived (§5.3)
            assert "{{" not in rendered

    def test_reduce_prompt_states_keep_and_discard_criteria(self) -> None:
        text = load_template(REDUCE).lower()
        assert "keep" in text and "discard" in text
        assert "claims" in text and "relationship" in text
        assert "narration" in text and "hedging" in text and "restatement" in text
        # inference is out: no "this suggests" / connecting facts into a third
        assert "inference" in text
        assert "this suggests" in text
        assert "traceable to a sentence" in text

    def test_tag_prompt_bounds_count_and_forbids_prose(self) -> None:
        text = load_template(TAG).lower()
        assert "2 to 6" in text  # a count band, not "tag everything"
        assert "primary subject" in text
        assert "in passing" in text  # mentions are not tags
        assert "no prose" in text and "parenthes" in text

    def test_organize_prompt_states_granularity_and_conflict_rules(self) -> None:
        text = load_template(ORGANIZE).lower()
        assert "one note per topic" in text
        assert "never one note per claim" in text
        assert "newer value wins" in text
        assert "create_note" in text and "update_note" in text
        assert "only" in text
        # the full valid-link-target list is offered, and dangling links are called out
        assert "{{existing_note_paths}}" in text
        assert "dangling link" in text
        # no invented sub-folders, no frontmatter in the body, stay on topic
        assert "sub-folder" in text
        assert "stay on the input's subject" in text
        assert "do **not** put a `---` frontmatter block" in text


class TestTagParsing:
    def test_accepts_normalized_tags(self) -> None:
        assert parse_tags("company\nvendor\nmulti-word") == ["company", "vendor", "multi-word"]

    def test_tolerates_bullets_and_blank_lines(self) -> None:
        assert parse_tags("- company\n\n* vendor\n") == ["company", "vendor"]

    def test_dedupes(self) -> None:
        assert parse_tags("company\ncompany\nvendor") == ["company", "vendor"]

    @pytest.mark.parametrize("bad", ["Company", "two words", "under_score", "trailing-", "café"])
    def test_rejects_non_normalized(self, bad: str) -> None:
        with pytest.raises(TerminalError):
            parse_tags(bad)

    def test_empty_output_is_rejected_not_coerced(self) -> None:
        with pytest.raises(TerminalError):
            parse_tags("\n\n")


class TestReduceParsing:
    def test_kept_items_one_per_line(self) -> None:
        raw = "- Acme was founded in 1996.\n- Acme ships Widget Platform.\n"
        assert parse_reduced_items(raw) == [
            "Acme was founded in 1996.",
            "Acme ships Widget Platform.",
        ]

    def test_empty_reduce_output_raises_terminal(self) -> None:
        with pytest.raises(TerminalError):
            parse_reduced_items("   \n  ")


class TestNoWritePath:
    def test_prompts_module_never_writes_a_tag_list(self) -> None:
        import groundtruth.ingest.prompts as mod

        public = {n for n in dir(mod) if not n.startswith("_")}
        for banned in ("save", "record", "write", "persist", "store", "append_tag"):
            assert banned not in public


class TestOrganizeUsesOnlyWriteTools:
    def test_multi_fact_input_maps_to_few_notes_via_write_tools_only(self) -> None:
        # Five distilled facts -> the model chooses to write two topic notes (ADR-9).
        budget = Budget(BudgetLimits(max_tool_calls=10))
        tools = WriteTools(vault_root="/vault", existing_paths=set())
        scripted = _ScriptedClient(
            [
                _calls(
                    (
                        "create_note",
                        {"folder": "companies", "title": "Acme", "body": "Founded 1996"},
                    ),
                    ("create_note", {"folder": "companies", "title": "Globex", "body": "Rival"}),
                ),
                LLMResponse(role="organize", model="m", text="done"),
            ]
        )
        outcome = run_agent(scripted, "organize", "organize prompt", tools, budget)

        assert outcome.status is AgentStatus.COMPLETED
        assert len(tools.pending) == 2  # few notes, not five
        assert {schema["function"]["name"] for schema in WriteTools.TOOL_SCHEMAS} == {
            "create_note",
            "update_note",
        }


class _ScriptedClient:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = responses

    def complete(self, *args: object, **kwargs: object) -> LLMResponse:
        return self._responses.pop(0)


def _calls(*specs: tuple[str, dict[str, str]]) -> LLMResponse:
    return LLMResponse(
        role="organize",
        model="m",
        text=None,
        tool_calls=[
            ToolCall(id=f"c{i}", name=name, arguments=args) for i, (name, args) in enumerate(specs)
        ],
    )
