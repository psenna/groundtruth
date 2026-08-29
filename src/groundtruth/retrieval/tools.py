"""Read-only vault tools shared by both pipelines: ``ls``, ``grep``, ``read`` (spec §8.1).

These are the *only* vault access an agent gets. They are structurally incapable
of writing, moving or deleting — that is invariant 1, enforced here, not by
convention. Every path argument routes through ``resolve_in_vault`` (#7) and every
call is charged to the budget (#12); once the budget is exhausted, calls refuse.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from ..storage.paths import resolve_in_vault
from .budget import Budget

#: Returned (not raised) by any tool once the budget is spent.
BUDGET_EXHAUSTED = "[budget exhausted: no further tool calls permitted]"

_MD_GLOB = "*.md"


class ReadOnlyTools:
    """A read-only view of one vault, metered by a :class:`Budget`."""

    def __init__(self, vault_root: Path | str, budget: Budget) -> None:
        self._root = Path(vault_root).resolve()
        self.budget = budget

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return self.TOOL_SCHEMAS

    def _resolve(self, path: str) -> Path:
        if path in ("", "."):
            return self._root
        return resolve_in_vault(self._root, path)

    def _charge(self) -> bool:
        if self.budget.exhausted:
            return False
        self.budget.record_tool_call()
        return True

    def _iter_markdown(self, base: Path) -> list[Path]:
        if base.is_file():
            return [base] if base.suffix == ".md" else []
        return sorted(p for p in base.rglob(_MD_GLOB) if p.is_file() and not p.is_symlink())

    def ls(self, path: str = ".") -> str:
        if not self._charge():
            return BUDGET_EXHAUSTED
        target = self._resolve(path)
        if not target.is_dir():
            return f"not a directory: {path}"
        entries = []
        for child in sorted(target.iterdir()):
            if child.is_symlink():
                continue
            entries.append(f"{child.name}/" if child.is_dir() else child.name)
        return "\n".join(entries) if entries else "(empty)"

    def grep(self, pattern: str, path: str = ".") -> str:
        if not self._charge():
            return BUDGET_EXHAUSTED
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return f"invalid pattern: {exc}"

        base = self._resolve(path)
        raw_matches: list[str] = []
        for file in self._iter_markdown(base):
            rel = file.relative_to(self._root).as_posix()
            for lineno, line in enumerate(file.read_text(encoding="utf-8").splitlines(), start=1):
                if regex.search(line):
                    raw_matches.append(f"{rel}:{lineno}: {line}")

        clamped = self.budget.clamp_matches(raw_matches)
        body = "\n".join(clamped.value) if clamped.value else "(no matches)"
        bounded = self.budget.clamp_grep_output(body)
        text = bounded.value
        notes = []
        if clamped.truncated:
            notes.append(f"{len(raw_matches) - len(clamped.value)} more matches omitted")
        if bounded.truncated:
            notes.append("output truncated at grep_max_bytes")
        if notes:
            text += "\n... [truncated: " + "; ".join(notes) + "]"
        return text

    def read(self, path: str) -> str:
        if not self._charge():
            return BUDGET_EXHAUSTED
        target = self._resolve(path)
        if target.is_symlink() or not target.is_file():
            return f"no such file: {path}"
        clamped = self.budget.clamp_read(target.read_text(encoding="utf-8"))
        if clamped.truncated:
            return clamped.value + "\n[... truncated at read_max_bytes ...]"
        return clamped.value

    def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        if name not in _DISPATCH:
            return f"unknown tool: {name}"
        return _DISPATCH[name](self, arguments)

    TOOL_SCHEMAS: ClassVar[list[dict[str, Any]]] = [
        {
            "type": "function",
            "function": {
                "name": "ls",
                "description": "List the entries of a directory inside the vault.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Vault-relative directory. Defaults to the vault root.",
                        }
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "grep",
                "description": (
                    "Search note text for a Python regular expression. Returns "
                    "'<path>:<line>: <text>' matches; truncation is always noted."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "A Python regular expression.",
                        },
                        "path": {
                            "type": "string",
                            "description": "Vault-relative path. Defaults to the root.",
                        },
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read",
                "description": (
                    "Return the full text of one note. Output is capped at "
                    "read_max_bytes with an explicit truncation marker."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Vault-relative note path."}
                    },
                    "required": ["path"],
                },
            },
        },
    ]


_DISPATCH: dict[str, Callable[[ReadOnlyTools, dict[str, Any]], str]] = {
    "ls": lambda t, a: t.ls(a.get("path", ".")),
    "grep": lambda t, a: t.grep(a["pattern"], a.get("path", ".")),
    "read": lambda t, a: t.read(a["path"]),
}


__all__ = ["BUDGET_EXHAUSTED", "ReadOnlyTools"]
