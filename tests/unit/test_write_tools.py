from __future__ import annotations

import inspect
import logging

import pytest

from groundtruth.ingest.write_tools import BUDGET_EXHAUSTED, PendingWrites, WriteTools
from groundtruth.retrieval.budget import Budget, BudgetLimits


def _tools(existing: set[str] | None = None) -> WriteTools:
    return WriteTools(vault_root="/vault", existing_paths=existing or set())


class TestSignatures:
    def test_create_note_has_no_path_parameter(self) -> None:
        params = list(inspect.signature(WriteTools.create_note).parameters)
        assert params == ["self", "folder", "title", "body"]

    def test_update_note_takes_path_and_body_only(self) -> None:
        params = list(inspect.signature(WriteTools.update_note).parameters)
        assert params == ["self", "path", "body"]

    def test_schemas_expose_exactly_the_intended_params(self) -> None:
        by_name = {s["function"]["name"]: s for s in WriteTools.TOOL_SCHEMAS}
        assert set(by_name) == {"create_note", "update_note"}
        assert set(by_name["create_note"]["function"]["parameters"]["properties"]) == {
            "folder",
            "title",
            "body",
        }
        assert set(by_name["update_note"]["function"]["parameters"]["properties"]) == {
            "path",
            "body",
        }

    def test_model_cannot_set_frontmatter_fields(self) -> None:
        for schema in WriteTools.TOOL_SCHEMAS:
            props = schema["function"]["parameters"]["properties"]
            for forbidden in ("sources", "created", "updated", "tags", "frontmatter"):
                assert forbidden not in props


class TestBuffering:
    def test_create_note_buffers_without_touching_disk(self, tmp_path: object) -> None:
        tools = WriteTools(vault_root=str(tmp_path), existing_paths=set())
        msg = tools.create_note("companies", "Acme Corp", "Acme ships widgets.")
        assert "companies/Acme Corp.md" in msg
        assert len(tools.pending) == 1
        assert tools.pending.notes[0].is_new is True
        assert list((tmp_path).iterdir()) == []  # nothing written  # type: ignore[attr-defined]

    def test_update_note_buffers(self, tmp_path: object) -> None:
        tools = WriteTools(vault_root=str(tmp_path), existing_paths={"companies/Acme Corp.md"})
        msg = tools.update_note("companies/Acme Corp.md", "new body")
        assert "updated" in msg
        assert tools.pending.notes[0].is_new is False

    def test_pending_writes_is_a_consumable_collection(self) -> None:
        tools = _tools()
        tools.create_note("f", "A", "a")
        tools.create_note("f", "B", "b")
        pending = tools.pending
        assert isinstance(pending, PendingWrites)
        assert [n.title for n in pending] == ["A", "B"]
        assert pending.paths == ["f/A.md", "f/B.md"]


class TestRules:
    def test_update_refuses_nonexistent_path(self) -> None:
        tools = _tools(existing={"companies/Real.md"})
        msg = tools.update_note("companies/Ghost.md", "x")
        assert "error" in msg.lower()
        assert len(tools.pending) == 0

    def test_creating_same_title_twice_is_detected(self) -> None:
        tools = _tools()
        assert "created" in tools.create_note("companies", "Acme", "first")
        second = tools.create_note("companies", "Acme", "second")
        assert "error" in second.lower()
        assert len(tools.pending) == 1

    def test_unsafe_title_is_reported_to_the_model(self) -> None:
        tools = _tools()
        msg = tools.create_note("companies", "../escape", "x")
        assert "error" in msg.lower()
        assert len(tools.pending) == 0

    def test_folder_traversal_is_reported(self) -> None:
        tools = _tools()
        msg = tools.create_note("../outside", "Note", "x")
        assert "error" in msg.lower()
        assert len(tools.pending) == 0


class TestArgumentNormalisation:
    def test_title_with_md_extension_is_stripped(self) -> None:
        tools = _tools()
        msg = tools.create_note("projects", "core_engine.md", "body")
        assert "projects/core_engine.md" in msg
        assert tools.pending.notes[0].path == "projects/core_engine.md"
        assert tools.pending.notes[0].title == "core_engine"

    def test_md_and_bare_title_collide_so_the_dup_check_can_catch_them(self) -> None:
        tools = _tools()
        assert "created" in tools.create_note("projects", "Usage", "a")
        second = tools.create_note("projects", "Usage.md", "b")  # same note, filename-y title
        assert "already staged" in second
        assert len(tools.pending) == 1

    def test_leading_frontmatter_block_is_removed_from_body(self) -> None:
        tools = _tools()
        body = "---\ntags:\n  - acme\n  - vendor\n---\n\n# Acme\n\nAcme ships widgets.\n"
        tools.create_note("companies", "Acme", body)
        assert tools.pending.notes[0].body == "# Acme\n\nAcme ships widgets.\n"

    def test_update_note_also_strips_body_frontmatter(self) -> None:
        tools = _tools({"companies/Acme.md"})
        tools.update_note("companies/Acme.md", "---\ntitle: Acme\n---\nnew body\n")
        assert tools.pending.notes[0].body == "new body\n"

    def test_a_stray_horizontal_rule_is_not_mistaken_for_frontmatter(self) -> None:
        tools = _tools()
        body = "---\nJust a themed break, not YAML.\n---\nMore text.\n"
        tools.create_note("f", "N", body)
        assert tools.pending.notes[0].body == body  # untouched

    def test_unfenced_frontmatter_lines_are_removed_from_body(self) -> None:
        tools = _tools()
        body = (
            "tags:\n- ai-sandbox\n- validation\nsources:\n- distilled-design-specification\n"
            "\n# AI Sandbox Overview\n\nReal content here.\n"
        )
        tools.create_note("f", "N", body)
        assert tools.pending.notes[0].body == "# AI Sandbox Overview\n\nReal content here.\n"

    def test_body_that_merely_starts_with_a_colon_word_is_left_alone(self) -> None:
        tools = _tools()
        body = "Note: this is ordinary prose that happens to start with a word and colon.\n"
        tools.create_note("f", "N", body)
        assert tools.pending.notes[0].body == body


class TestCollisionCoercion:
    def test_create_over_an_existing_path_is_buffered_as_an_update(self) -> None:
        tools = _tools({"companies/Acme Corp.md"})
        msg = tools.create_note("companies", "Acme Corp", "New body.")
        assert len(tools.pending) == 1
        note = tools.pending.notes[0]
        assert note.path == "companies/Acme Corp.md"
        assert note.is_new is False
        assert note.body == "New body."
        assert "created" not in msg
        assert "updated" in msg

    def test_coerced_note_is_shaped_exactly_like_an_update(self) -> None:
        tools = _tools({"companies/Acme Corp.md"})
        tools.create_note("companies", "Acme Corp", "New body.")
        note = tools.pending.notes[0]
        assert note.folder is None and note.title is None

    def test_title_normalisation_runs_before_the_collision_check(self) -> None:
        tools = _tools({"projects/Usage.md"})
        tools.create_note("projects", "Usage.md", "b")
        assert tools.pending.notes[0].is_new is False

    def test_unsafe_title_still_errors_even_when_a_note_exists(self) -> None:
        tools = _tools({"companies/escape.md"})
        msg = tools.create_note("companies", "../escape", "x")
        assert "error" in msg.lower()
        assert len(tools.pending) == 0

    def test_same_path_twice_in_one_batch_still_errors_when_the_path_exists(self) -> None:
        tools = _tools({"f/A.md"})
        first = tools.create_note("f", "A", "x")
        assert "updated" in first
        second = tools.create_note("f", "A", "x")
        assert "already staged" in second
        assert len(tools.pending) == 1

    def test_the_coercion_is_logged_to_the_stage_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="groundtruth.jobs"):
            tools = WriteTools(
                vault_root="/vault",
                existing_paths={"companies/Acme.md"},
                job_id="01JOB",
                vault_name="work",
            )
            tools.create_note("companies", "Acme", "New body.")
        records = [r for r in caplog.records if r.name == "groundtruth.jobs"]
        assert len(records) == 1
        payload = records[0].groundtruth
        assert payload["stage"] == "llm"
        assert payload["status"] == "coerce"
        assert payload["path"] == "companies/Acme.md"
        assert payload["job_id"] == "01JOB"

    def test_update_to_a_missing_path_is_still_refused_and_never_becomes_a_create(self) -> None:
        tools = _tools({"companies/Real.md"})
        msg = tools.update_note("companies/Ghost.md", "x")
        assert "error" in msg.lower()
        assert tools.pending.paths == []


class TestDispatch:
    def test_dispatch_routes_to_the_named_tool(self) -> None:
        tools = _tools({"a/b.md"})
        assert "created" in tools.dispatch(
            "create_note", {"folder": "f", "title": "T", "body": "x"}
        )
        assert "updated" in tools.dispatch("update_note", {"path": "a/b.md", "body": "y"})

    def test_no_filesystem_mutation_methods(self) -> None:
        public = {n for n in dir(WriteTools) if not n.startswith("_")}
        for banned in ("commit", "flush", "apply", "write", "delete", "remove", "save"):
            assert banned not in public


class TestBudget:
    def test_each_dispatch_charges_the_budget_and_stops_when_spent(self) -> None:
        budget = Budget(BudgetLimits(max_tool_calls=2))
        tools = WriteTools(vault_root="/vault", existing_paths=set(), budget=budget)

        assert "created" in tools.dispatch(
            "create_note", {"folder": "f", "title": "A", "body": "x"}
        )
        assert "created" in tools.dispatch(
            "create_note", {"folder": "f", "title": "B", "body": "x"}
        )
        # third call: budget spent, refuses, note not buffered
        assert tools.dispatch("create_note", {"folder": "f", "title": "C", "body": "x"}) == (
            BUDGET_EXHAUSTED
        )
        assert len(tools.pending) == 2
        assert budget.exhausted

    def test_unmetered_when_no_budget_given(self) -> None:
        tools = _tools()
        for i in range(50):
            tools.dispatch("create_note", {"folder": "f", "title": f"N{i}", "body": "x"})
        assert len(tools.pending) == 50
