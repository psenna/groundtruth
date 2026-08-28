"""Wikilink extraction and link-integrity checking (spec §7.6, §6).

Every ``[[link]]`` in a note body must resolve to a note that exists or to one
created in the same job — a dangling link breaks navigation and yields citations
pointing at nothing. Reused by recovery's grounding check (#25), so this module
holds no ingest-specific assumptions.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass

_FENCED_CODE = re.compile(r"(?ms)^[ \t]*(```|~~~).*?^[ \t]*\1[ \t]*$")
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_WIKILINK = re.compile(r"\[\[([^\[\]|]+?)(?:\|([^\[\]]*))?\]\]")


@dataclass(frozen=True)
class Link:
    target: str
    display: str | None = None


@dataclass(frozen=True)
class Dangling:
    target: str
    display: str | None = None


def _strip_code(text: str) -> str:
    text = _FENCED_CODE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return _INLINE_CODE.sub("", text)


def extract_links(body: str) -> list[Link]:
    """Return every wikilink in ``body``, ignoring those inside code spans/blocks."""
    links: list[Link] = []
    for raw_target, raw_display in _WIKILINK.findall(_strip_code(body)):
        target = raw_target.split("#", 1)[0].strip()
        if not target:
            continue
        display = raw_display.strip() if raw_display else None
        links.append(Link(target=target, display=display or None))
    return links


def _index(paths: Iterable[str]) -> set[str]:
    """Map each note path to the strings a link may use to reach it."""
    keys: set[str] = set()
    for path in paths:
        stem = path[:-3] if path.endswith(".md") else path
        keys.add(stem)  # folder/Name
        keys.add(stem.rsplit("/", 1)[-1])  # bare Name
    return keys


def check_links(
    links: Iterable[Link],
    existing: Collection[str],
    created_this_job: Collection[str],
) -> list[Dangling]:
    """Return the links that resolve to no known note (existing or created this job)."""
    resolvable = _index(existing) | _index(created_this_job)
    return [
        Dangling(target=link.target, display=link.display)
        for link in links
        if link.target not in resolvable
    ]


__all__ = ["Dangling", "Link", "check_links", "extract_links"]
