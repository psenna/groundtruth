from __future__ import annotations

import os
import tempfile
import unicodedata
from collections.abc import Iterator
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from groundtruth.errors import TerminalError
from groundtruth.storage.paths import UnsafePathError, resolve_in_vault, sanitize_title


@pytest.fixture(scope="module")
def vault() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw).resolve() / "vault"
        root.mkdir()
        yield root


class TestSanitizeTitle:
    @pytest.mark.parametrize(
        "bad",
        [
            "../etc/passwd",
            "..\\windows",
            "/absolute",
            "~",
            "~/secrets",
            "a/b",
            "a\\b",
            "a:b",
            "with\x00null",
            ".hidden",
            "..",
            "trailing ",
            "trailing.",
            "CON",
            "nul",
            "Aux.md",
            "COM1",
            "LPT9",
            "CONIN$",
            "clock$",
            "COM\u00b9",  # superscript 1 -> NFKC "COM1"
            "\uff0fetc\uff0fpasswd",  # fullwidth solidus -> NFKC "/"
            "",
            "   ",
        ],
    )
    def test_rejected(self, bad: str) -> None:
        with pytest.raises(TerminalError):
            sanitize_title(bad)

    def test_safe_unicode_preserved(self) -> None:
        assert sanitize_title("Café Ω 日本語") == "Café Ω 日本語"

    def test_result_is_nfc_normalized(self) -> None:
        decomposed = unicodedata.normalize("NFD", "Café")
        assert sanitize_title(decomposed) == unicodedata.normalize("NFC", "Café")

    def test_overlong_truncated_deterministically(self) -> None:
        long = "x" * 500
        assert sanitize_title(long) == sanitize_title(long)
        assert len(sanitize_title(long).encode()) <= 200

    def test_overlong_truncation_avoids_collision(self) -> None:
        a = "x" * 300 + "-alpha"
        b = "x" * 300 + "-beta"
        assert sanitize_title(a) != sanitize_title(b)


class TestResolveInVault:
    def test_simple_path_inside(self, vault: Path) -> None:
        p = resolve_in_vault(vault, "companies", "Acme Corp.md")
        assert p == vault / "companies" / "Acme Corp.md"

    def test_multi_segment_folder_part_is_fine(self, vault: Path) -> None:
        p = resolve_in_vault(vault, "projects/internal", "x.md")
        assert p == vault / "projects" / "internal" / "x.md"

    @pytest.mark.parametrize(
        "parts",
        [
            ("..",),
            ("..", "escape.md"),
            ("companies", "..", "..", "etc"),
            ("companies/../../etc", "x.md"),
            ("/etc/passwd",),
            ("with\x00null.md",),
            ("~", "x.md"),
            ("a\\b", "c.md"),  # backslash rejected, not rewritten
            ("a//b", "c.md"),  # empty segment
            ("a/", "c.md"),
            (".",),
            ("companies", "."),
            ("C:/Windows", "evil.md"),  # drive-relative
            (".obsidian", "plugins", "x", "main.js"),  # dotfile dir
            (".git", "hooks", "post-commit"),
            ("x" * 4000,),  # absurd length
        ],
    )
    def test_rejected(self, vault: Path, parts: tuple[str, ...]) -> None:
        with pytest.raises(UnsafePathError) as excinfo:
            resolve_in_vault(vault, *parts)
        assert excinfo.value.stage == "write-validation"

    def test_symlinked_folder_escape_rejected(self, vault: Path, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        link = vault / "sneaky"
        link.symlink_to(outside)
        try:
            with pytest.raises(TerminalError):
                resolve_in_vault(vault, "sneaky", "loot.md")
        finally:
            link.unlink()

    def test_symlink_loop_is_rejected_cleanly(self, vault: Path) -> None:
        # The CPython <= 3.12 resolve() bypass: a loop + a ..-relative folder link.
        loop = vault / "loop"
        notes = vault / "Notes"
        out = vault / "out"
        loop.symlink_to("loop")
        notes.symlink_to("loop/../out")
        out.symlink_to("/tmp")
        try:
            with pytest.raises(UnsafePathError):
                resolve_in_vault(vault, "Notes", "loot.md")
        finally:
            for link in (loop, notes, out):
                link.unlink()

    def test_self_referential_symlink_rejected(self, vault: Path) -> None:
        link = vault / "self"
        link.symlink_to("self")
        try:
            with pytest.raises(UnsafePathError):
                resolve_in_vault(vault, "self", "note.md")
        finally:
            link.unlink()

    def test_sibling_prefix_directory_is_not_containment(self, tmp_path: Path) -> None:
        root = (tmp_path / "vault").resolve()
        root.mkdir()
        (tmp_path / "vault-evil").mkdir()
        # No way to name the sibling without a rejected '..', but assert the
        # parents check is path-based, not string-prefix based.
        inside = resolve_in_vault(root, "n.md")
        assert root in inside.parents


class TestProperty:
    @given(st.text())
    def test_resolved_path_is_inside_vault_or_raises(self, s: str) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            try:
                resolved = resolve_in_vault(root, f"{sanitize_title(s)}.md")
            except TerminalError:
                return
            assert root in resolved.parents

    @given(st.lists(st.text(), min_size=1, max_size=6))
    def test_multi_part_property(self, parts: list[str]) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            try:
                resolved = resolve_in_vault(root, *parts)
            except TerminalError:
                return
            real = Path(os.path.realpath(resolved))
            assert real == root or root in real.parents

    @given(
        link_targets=st.lists(
            st.sampled_from(["..", "../..", "/tmp", "loopself", "sub/../.."]),
            min_size=1,
            max_size=4,
        ),
        parts=st.lists(st.sampled_from(["a", "b", "note.md", "sub"]), min_size=1, max_size=4),
    )
    def test_symlink_forest_never_escapes(self, link_targets: list[str], parts: list[str]) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve() / "v"
            root.mkdir()
            for i, target in enumerate(link_targets):
                name = root / f"link{i}"
                actual = f"link{i}" if target == "loopself" else target
                try:
                    name.symlink_to(actual)
                except OSError:
                    continue
            all_parts = [*parts, *(f"link{i}" for i in range(len(link_targets)))]
            try:
                resolved = resolve_in_vault(root, *all_parts)
            except TerminalError:
                return
            real = Path(os.path.realpath(resolved))
            assert real == root or root in real.parents
