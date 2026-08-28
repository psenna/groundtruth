from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from groundtruth.ingest import schema as schema_module
from groundtruth.ingest.schema import (
    SchemaError,
    SchemaNotFoundError,
    SchemaWriteRefusedError,
    load_schema,
    parse_schema,
    write_schema,
)

TEMPLATE = """\
# Schema

## Folders
<!-- Describe how notes are organized. -->
- companies/ — organizations
- people/ — individuals
- projects/ — ongoing work

## Tags
<!-- guidance, not an inventory -->
- Use `vendor` for suppliers, not `supplier`.
- Prefer `project` over `initiative`.
"""


class TestParse:
    def test_parses_declared_folders(self) -> None:
        assert parse_schema(TEMPLATE).folders == ["companies", "people", "projects"]

    def test_keeps_prescriptive_tag_text(self) -> None:
        guidance = parse_schema(TEMPLATE).tag_guidance
        assert "Use `vendor` for suppliers" in guidance
        assert "initiative" in guidance

    def test_tolerates_prose_reordering_and_extra_sections(self) -> None:
        text = """\
# My vault

Some notes about how I think about this.

## Workflow
I ingest things weekly.

## Tags
- lowercase please

## Folders
- projects/
- archive/ — cold storage

## More prose
whatever
"""
        assert parse_schema(text).folders == ["projects", "archive"]

    def test_missing_folders_section_is_a_clear_error(self) -> None:
        with pytest.raises(SchemaError, match="Folders"):
            parse_schema("# Schema\n\n## Tags\n- x\n")

    def test_empty_folders_section_is_a_clear_error(self) -> None:
        with pytest.raises(SchemaError, match="no folders"):
            parse_schema("# Schema\n\n## Folders\n\nnothing here\n")


class TestLoad:
    def test_load_from_vault_dir(self, tmp_path: Path) -> None:
        (tmp_path / "schema.md").write_text(TEMPLATE)
        assert load_schema(tmp_path).folders == ["companies", "people", "projects"]

    def test_missing_schema_md_is_a_distinct_error(self, tmp_path: Path) -> None:
        with pytest.raises(SchemaNotFoundError):
            load_schema(tmp_path)


class TestWriteProtection:
    def test_public_surface_has_no_ingestion_write_path(self) -> None:
        public = {n for n in dir(schema_module) if not n.startswith("_")}
        # The only writer is write_schema, and it is gated by `allowed`.
        for banned in ("append_folder", "add_tag", "update_schema", "merge_schema", "evolve"):
            assert banned not in public

    def test_write_schema_refuses_when_not_allowed(self, tmp_path: Path) -> None:
        with pytest.raises(SchemaWriteRefusedError):
            write_schema(tmp_path, "# Schema\n\n## Folders\n- x/\n", allowed=False)
        assert not (tmp_path / "schema.md").exists()

    def test_write_schema_allowed_preserves_content_exactly(self, tmp_path: Path) -> None:
        content = "# Schema\n\n## Folders\n- a/\n\narbitrary user text 🚀\n"
        write_schema(tmp_path, content, allowed=True)
        assert (tmp_path / "schema.md").read_text(encoding="utf-8") == content

    def test_write_schema_requires_allowed_keyword(self) -> None:
        params = inspect.signature(write_schema).parameters
        assert params["allowed"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_ingest_package_never_calls_write_schema(self) -> None:
        ingest_dir = Path(schema_module.__file__).parent
        for py in ingest_dir.glob("*.py"):
            if py.name == "schema.py":
                continue
            assert "write_schema" not in py.read_text(encoding="utf-8")
