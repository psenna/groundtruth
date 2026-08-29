"""Ingestion prompts for the tag / reduce / organize roles (spec §7.5, ADR-9).

The prompts are Markdown files in this directory, versioned with the code — not
inline strings. ``render_prompt`` fills ``{{PLACEHOLDER}}`` tokens; the tag/reduce
output parsers **reject** malformed output rather than coercing it. Nothing here
writes anywhere: a new tag becomes part of the vocabulary only by appearing in a
committed note's frontmatter (ADR-12).
"""

from __future__ import annotations

import re
from pathlib import Path

from ...errors import MalformedLLMOutputError

TAG = "tag"
REDUCE = "reduce"
ORGANIZE = "organize"
ROLES = (TAG, REDUCE, ORGANIZE)

_PROMPT_DIR = Path(__file__).parent
_NORMALIZED_TAG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_STAGE = "llm"


def load_template(role: str) -> str:
    """Return the raw prompt template for ``role``."""
    if role not in ROLES:
        raise ValueError(f"unknown prompt role {role!r}; expected one of {ROLES}")
    return (_PROMPT_DIR / f"{role}.md").read_text(encoding="utf-8")


def render_prompt(role: str, **context: str) -> str:
    """Render ``role``'s template, substituting ``{{KEY}}`` for each ``key=value``."""
    text = load_template(role)
    for key, value in context.items():
        text = text.replace("{{" + key.upper() + "}}", value)
    return text


def _clean_line(line: str) -> str:
    return line.strip().lstrip("-*").strip().strip("`").strip()


def parse_tags(raw: str) -> list[str]:
    """Parse the tag step's output. Every tag must already be normalized, or the ingest fails."""
    tags: list[str] = []
    for line in raw.splitlines():
        candidate = _clean_line(line)
        if not candidate:
            continue
        if not _NORMALIZED_TAG.match(candidate):
            raise MalformedLLMOutputError(
                f"tag {candidate!r} is not normalized (lowercase, hyphen-separated)",
                stage=_STAGE,
            )
        if candidate not in tags:
            tags.append(candidate)
    if not tags:
        raise MalformedLLMOutputError("tag step produced no tags", stage=_STAGE)
    return tags


def parse_reduced_items(raw: str) -> list[str]:
    """Parse the reduce step's output into kept items, one per line."""
    items = [cleaned for line in raw.splitlines() if (cleaned := _clean_line(line))]
    if not items:
        raise MalformedLLMOutputError("reduce step produced no items", stage=_STAGE)
    return items


__all__ = [
    "ORGANIZE",
    "REDUCE",
    "ROLES",
    "TAG",
    "load_template",
    "parse_reduced_items",
    "parse_tags",
    "render_prompt",
]
