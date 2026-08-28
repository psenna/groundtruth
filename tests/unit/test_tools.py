from __future__ import annotations

from pathlib import Path

import pytest

from groundtruth.retrieval.budget import Budget, BudgetLimits
from groundtruth.retrieval.tools import BUDGET_EXHAUSTED, ReadOnlyTools
from groundtruth.storage.paths import UnsafePathError

pytestmark = pytest.mark.integration


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "work" / "vault"
    (root / "companies").mkdir(parents=True)
    (root / "companies" / "Acme.md").write_text(
        "---\ntitle: Acme\ntags: [company]\nsources: []\n"
        "created: 2026-01-01\nupdated: 2026-01-01\n---\n\n"
        "Acme ships widgets.\nAcme was founded in 1996.\n"
    )
    (root / "schema.md").write_text("# Schema\n")
    return root


def _tools(vault: Path, **limit_overrides: int) -> ReadOnlyTools:
    return ReadOnlyTools(vault, Budget(BudgetLimits(**limit_overrides)))


class TestToolSurface:
    def test_exactly_three_read_only_tools(self, vault: Path) -> None:
        names = {schema["function"]["name"] for schema in ReadOnlyTools.TOOL_SCHEMAS}
        assert names == {"ls", "grep", "read"}

    def test_no_write_move_or_delete_tool(self, vault: Path) -> None:
        names = {schema["function"]["name"] for schema in ReadOnlyTools.TOOL_SCHEMAS}
        for forbidden in ("write", "create", "update", "delete", "move", "rm", "put"):
            assert forbidden not in names

    def test_schemas_are_llm_shaped(self, vault: Path) -> None:
        for schema in ReadOnlyTools.TOOL_SCHEMAS:
            assert schema["type"] == "function"
            assert set(schema["function"]) >= {"name", "description", "parameters"}
            assert schema["function"]["parameters"]["type"] == "object"


class TestLs:
    def test_lists_directory_inside_vault(self, vault: Path) -> None:
        out = _tools(vault).ls("companies")
        assert "Acme.md" in out

    def test_lists_vault_root(self, vault: Path) -> None:
        out = _tools(vault).ls(".")
        assert "companies/" in out
        assert "schema.md" in out

    def test_refuses_path_outside_vault(self, vault: Path) -> None:
        with pytest.raises(UnsafePathError):
            _tools(vault).ls("..")


class TestGrep:
    def test_returns_path_and_line_numbers(self, vault: Path) -> None:
        out = _tools(vault).grep("founded")
        assert "companies/Acme.md:10:" in out  # line 10 of the file
        assert "founded in 1996" in out

    def test_truncates_at_max_matches_and_says_so(self, vault: Path) -> None:
        (vault / "many.md").write_text("hit\n" * 20)
        out = _tools(vault, grep_max_matches=3).grep("hit")
        assert out.count("\n") <= 6
        assert "truncat" in out.lower()

    def test_truncates_at_max_bytes_and_says_so(self, vault: Path) -> None:
        (vault / "big.md").write_text("match here\n" * 500)
        out = _tools(vault, grep_max_bytes=80).grep("match")
        assert len(out.encode()) < 400
        assert "truncat" in out.lower()

    def test_traversal_is_refused(self, vault: Path) -> None:
        with pytest.raises(UnsafePathError):
            _tools(vault).grep("x", "../../etc")


class TestRead:
    def test_returns_content(self, vault: Path) -> None:
        out = _tools(vault).read("companies/Acme.md")
        assert "Acme ships widgets." in out

    def test_truncates_with_explicit_marker(self, vault: Path) -> None:
        (vault / "long.md").write_text("x" * 5000)
        out = _tools(vault, read_max_bytes=100).read("long.md")
        assert len(out.encode()) < 400
        assert "truncat" in out.lower()

    def test_missing_file_message(self, vault: Path) -> None:
        out = _tools(vault).read("companies/Ghost.md")
        assert "Ghost.md" in out

    def test_traversal_is_refused(self, vault: Path) -> None:
        with pytest.raises(UnsafePathError):
            _tools(vault).read("../secrets.md")


class TestBudget:
    def test_every_tool_decrements_budget(self, vault: Path) -> None:
        tools = _tools(vault, max_tool_calls=30)
        budget = tools.budget
        tools.ls(".")
        tools.grep("Acme")
        tools.read("companies/Acme.md")
        assert budget.tool_calls == 3

    def test_exhausted_budget_refuses_without_executing(self, vault: Path) -> None:
        tools = _tools(vault, max_tool_calls=1)
        assert tools.ls(".") != BUDGET_EXHAUSTED
        assert tools.ls(".") == BUDGET_EXHAUSTED
        assert tools.grep("Acme") == BUDGET_EXHAUSTED
        assert tools.read("companies/Acme.md") == BUDGET_EXHAUSTED
        assert tools.budget.tool_calls == 1  # the refused calls did not count
