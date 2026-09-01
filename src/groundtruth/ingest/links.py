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

_FENCE_MARKER = re.compile(r"^[ \t]*(```+|~~~+)")
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
    """Blank out fenced and inline code so links inside them are ignored.

    Fence handling is a linear line scan — a regex spanning ``.*?`` across
    newlines backtracks quadratically on an unterminated fence in model output.
    """
    lines = text.split("\n")
    out: list[str] = []
    fence: str | None = None
    for line in lines:
        marker = _FENCE_MARKER.match(line)
        if fence is None and marker:
            fence = marker.group(1)[0]  # ` or ~
            out.append("")
            continue
        if fence is not None:
            out.append("")
            if marker and marker.group(1)[0] == fence:
                fence = None
            continue
        out.append(line)
    return _INLINE_CODE.sub("", "\n".join(out))


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


def downgrade_links(body: str, targets: Collection[str]) -> tuple[str, list[str]]:
    """Rewrite ``[[target]]`` / ``[[target|alias]]`` to plain text for every link
    whose target is in ``targets`` — the alias text if the link had one, else the
    raw target. Links inside fenced blocks or inline code are left untouched
    (``extract_links`` never treats them as links either). Returns the new body
    and the targets actually downgraded, in first-seen order.

    Used only by the terminal dangling-link downgrade of §7.6: it removes the
    ``[[ ]]`` markup and nothing else.
    """
    wanted = set(targets)
    downgraded: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        target = match.group(1).split("#", 1)[0].strip()
        if target not in wanted:
            return match.group(0)
        if target not in downgraded:
            downgraded.append(target)
        alias = (match.group(2) or "").strip()
        return alias or target

    out: list[str] = []
    fence: str | None = None
    for line in body.split("\n"):
        marker = _FENCE_MARKER.match(line)
        if fence is None and marker:
            fence = marker.group(1)[0]
            out.append(line)
            continue
        if fence is not None:
            out.append(line)
            if marker and marker.group(1)[0] == fence:
                fence = None
            continue
        segments = re.split(r"(`[^`\n]*`)", line)  # code spans land at odd indices
        for i in range(0, len(segments), 2):
            segments[i] = _WIKILINK.sub(_replace, segments[i])
        out.append("".join(segments))
    return "\n".join(out), downgraded


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


__all__ = ["Dangling", "Link", "check_links", "downgrade_links", "extract_links"]
