from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, ConfigDict, field_validator

#: Normalized tag: lowercase words separated by single hyphens (spec §5.3).
_TAG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
#: Lowercase hex SHA-256 digest.
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class NoteFrontmatter(BaseModel):
    """Required YAML frontmatter on every system-written note (spec §6)."""

    model_config = ConfigDict(extra="forbid")

    title: str
    tags: list[str]
    #: Append-only list of SHA-256 hashes of the sources this note draws on.
    sources: list[str]
    created: date
    updated: date

    @field_validator("tags")
    @classmethod
    def _tags_normalized(cls, tags: list[str]) -> list[str]:
        for tag in tags:
            if not _TAG_RE.match(tag):
                raise ValueError(
                    f"tag {tag!r} is not normalized: expected lowercase words "
                    "separated by single hyphens (no spaces, uppercase or underscores)"
                )
        return tags

    @field_validator("sources")
    @classmethod
    def _sources_are_sha256(cls, sources: list[str]) -> list[str]:
        for source in sources:
            if not _SHA256_RE.match(source):
                raise ValueError(f"source {source!r} is not a lowercase hex SHA-256 digest")
        return sources


class Note(BaseModel):
    """A vault note: its vault-relative path, frontmatter, and prose body."""

    model_config = ConfigDict(extra="forbid")

    #: Path relative to the vault directory, e.g. ``companies/Acme Corp.md``.
    path: str
    frontmatter: NoteFrontmatter
    body: str
