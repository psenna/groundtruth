from __future__ import annotations

import inspect

from groundtruth.ingest.write_tools import PendingWrites, WriteTools


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
