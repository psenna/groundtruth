"""Minimal, safe note rendering for the Browse view (spec §10.3, §38).

Note bodies originate from ingested text — untrusted. Everything is HTML-escaped
first; only a small, fixed set of Markdown-ish transforms is then applied, and
``[[wikilinks]]`` become Browse links (or a visibly broken span if the target
does not exist).
"""

from __future__ import annotations

import html
import re
from collections.abc import Collection

from ..ingest.links import Link, check_links, extract_links

_WIKILINK = re.compile(r"\[\[([^\[\]|]+?)(?:\|([^\[\]]*))?\]\]")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")


def _resolve(target: str, existing: Collection[str]) -> str | None:
    """Return the vault-relative path a link resolves to, or ``None`` if dangling."""
    dangling = {d.target for d in check_links([Link(target=target)], existing, set())}
    if target in dangling:
        return None
    for path in existing:
        stem = path[:-3] if path.endswith(".md") else path
        if target in (stem, stem.rsplit("/", 1)[-1]):
            return path
    return None


def _linkify(escaped_line: str, vault: str, existing: Collection[str]) -> str:
    def repl(match: re.Match[str]) -> str:
        raw = html.unescape(match.group(1)).strip()
        label = html.escape((match.group(2) or match.group(1)).strip())
        resolved = _resolve(raw, existing)
        if resolved is None:
            return f'<span class="broken-link" title="no such note">[[{label}]]</span>'
        return f'<a href="/browse/{html.escape(vault)}/{html.escape(resolved)}">[[{label}]]</a>'

    return _WIKILINK.sub(repl, escaped_line)


def render_note_body(body: str, *, vault: str, existing_paths: Collection[str]) -> str:
    """Render ``body`` to safe HTML."""
    out: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            out.append("<p>" + "<br>".join(paragraph) + "</p>")
            paragraph.clear()

    for raw_line in body.splitlines():
        escaped = html.escape(raw_line)
        escaped = _INLINE_CODE.sub(r"<code>\1</code>", escaped)
        escaped = _BOLD.sub(r"<strong>\1</strong>", escaped)
        heading = _HEADING.match(raw_line)
        if heading:
            flush()
            level = len(heading.group(1))
            inner = _linkify(html.escape(heading.group(2)), vault, existing_paths)
            out.append(f"<h{level}>{inner}</h{level}>")
            continue
        if not raw_line.strip():
            flush()
            continue
        paragraph.append(_linkify(escaped, vault, existing_paths))
    flush()
    return "\n".join(out)


def wikilink_targets(body: str) -> list[str]:
    return [link.target for link in extract_links(body)]


__all__ = ["render_note_body", "wikilink_targets"]
