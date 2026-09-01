"""Grounding runtime check — layer 1, applied to every answer (spec §9.1, invariant 3).

Two checks before an answer is returned:

1. it contains at least one ``[[citation]]``;
2. every cited note exists on disk in that citation's vault.

**Failing either downgrades the answer to a refusal — totally.** There is no
hedged, annotated, or partially-stripped answer. This catches fabricated note
names, the cheapest and most common grounding failure. A fabricated claim under a
real citation is §9.2's job, not this one.
"""

from __future__ import annotations

from ..ingest.links import Link, check_links, extract_links
from ..models import AnswerResult, Citation, Refusal, Vault

_SCHEMA_FILENAME = "schema.md"


def _existing_paths(vault: Vault) -> set[str]:
    root = vault.vault_dir
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.md")
        if path.is_file() and not path.is_symlink() and path.name != _SCHEMA_FILENAME
    }


def _to_citation(link: Link, default_vault: str) -> Citation:
    """Read ``[[vault:path]]`` (§8.3 future form) or ``[[path]]`` into a structured citation."""
    prefix, sep, rest = link.target.partition(":")
    if sep and prefix and "/" not in prefix:
        return Citation(vault=prefix, path=rest)
    return Citation(vault=default_vault, path=link.target)


def _dedupe(citations: list[Citation]) -> list[Citation]:
    seen: set[tuple[str, str]] = set()
    out: list[Citation] = []
    for citation in citations:
        key = (citation.vault, citation.path)
        if key not in seen:
            seen.add(key)
            out.append(citation)
    return out


def check_grounding(answer: AnswerResult, vault: Vault) -> AnswerResult | Refusal:
    """Return ``answer`` with verified citations, or a ``Refusal(no_evidence)``.

    The downgrade is total — this function never returns a modified or filtered
    answer, only the original or a refusal.
    """
    links = extract_links(answer.text)
    if not links:
        return Refusal(reason="no_evidence", token_usage=answer.token_usage)

    citations = [_to_citation(link, vault.name) for link in links]

    # Every citation is validated against its OWN vault, not an ambient one.
    if any(citation.vault != vault.name for citation in citations):
        return Refusal(reason="no_evidence", token_usage=answer.token_usage)

    existing = _existing_paths(vault)
    same_vault_links = [Link(target=citation.path) for citation in citations]
    if check_links(same_vault_links, existing, set()):
        return Refusal(reason="no_evidence", token_usage=answer.token_usage)

    return answer.model_copy(update={"citations": _dedupe(citations)})


__all__ = ["check_grounding"]
