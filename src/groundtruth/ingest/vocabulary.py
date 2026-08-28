"""Derived tag vocabulary (spec §5.3, ADR-12).

The *active* vocabulary is **computed** from note frontmatter, never stored — a
written list is a second copy of what already lives in every note and the two
drift. The result is cached against the vault's git ``HEAD`` sha, so it
invalidates exactly when the vault changes. The cache lives in the state dir,
never in the vault.
"""

from __future__ import annotations

import json
import warnings
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..models import Vault
from ..storage.frontmatter import FrontmatterError, parse_note
from ..storage.git import GitRepo

_DEFAULT_VOCAB_MAX_BYTES = 4096
_SCHEMA_FILENAME = "schema.md"


@dataclass(frozen=True)
class TagCount:
    tag: str
    count: int


@dataclass(frozen=True)
class Vocabulary:
    """Frequency-ranked tags plus whether the byte budget truncated the list."""

    tags: list[TagCount]
    total_tags: int
    truncated: bool
    omitted: int
    from_cache: bool = False

    def render(self) -> str:
        """Render for prompt injection. The byte cap was already applied at derive time."""
        text = "\n".join(f"{tag.tag} ({tag.count})" for tag in self.tags)
        if self.truncated:
            text += f"\n[... {self.omitted} more tags omitted, vocabulary truncated]"
        return text


def _rank(counter: Counter[str]) -> list[TagCount]:
    return [TagCount(tag=tag, count=count) for tag, count in sorted(counter.items(), key=_order)]


def _order(item: tuple[str, int]) -> tuple[int, str]:
    tag, count = item
    return (-count, tag)


def _scan_tags(vault_dir: Path) -> Counter[str]:
    counter: Counter[str] = Counter()
    for path in sorted(vault_dir.rglob("*.md")):
        if path.is_symlink() or not path.is_file() or path.name == _SCHEMA_FILENAME:
            continue
        rel = path.relative_to(vault_dir).as_posix()
        try:
            note = parse_note(path.read_text(encoding="utf-8"), path=rel)
        except FrontmatterError as exc:
            warnings.warn(f"skipping {rel}: {exc}", stacklevel=2)
            continue
        counter.update(note.frontmatter.tags)
    return counter


def _budget(ranked: list[TagCount], max_bytes: int) -> Vocabulary:
    kept: list[TagCount] = []
    used = 0
    for i, tag in enumerate(ranked):
        line = f"{tag.tag} ({tag.count})"
        addition = len(line.encode()) + (1 if kept else 0)
        if kept and used + addition > max_bytes:
            return Vocabulary(
                tags=kept, total_tags=len(ranked), truncated=True, omitted=len(ranked) - i
            )
        kept.append(tag)
        used += addition
    return Vocabulary(tags=kept, total_tags=len(ranked), truncated=False, omitted=0)


def _cache_path(state_dir: Path, vault_name: str) -> Path:
    return Path(state_dir) / "vocab_cache" / f"{vault_name}.json"


def derive_vocabulary(
    vault: Vault,
    *,
    state_dir: Path | str,
    vocab_max_bytes: int = _DEFAULT_VOCAB_MAX_BYTES,
) -> Vocabulary:
    """Return the frequency-ranked tag vocabulary for ``vault``, HEAD-sha cached."""
    head = GitRepo(vault.repo_root).head_sha()
    cache_file = _cache_path(Path(state_dir), vault.name)

    if cache_file.is_file():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        if cached.get("head") == head:
            ranked = [TagCount(tag=t, count=c) for t, c in cached["tags"]]
            result = _budget(ranked, vocab_max_bytes)
            return Vocabulary(
                tags=result.tags,
                total_tags=result.total_tags,
                truncated=result.truncated,
                omitted=result.omitted,
                from_cache=True,
            )

    ranked = _rank(_scan_tags(vault.vault_dir))
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps({"head": head, "tags": [[t.tag, t.count] for t in ranked]}),
        encoding="utf-8",
    )
    return _budget(ranked, vocab_max_bytes)


__all__ = ["TagCount", "Vocabulary", "derive_vocabulary"]
