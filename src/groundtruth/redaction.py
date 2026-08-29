"""Shared secret detection and redaction (invariant 6, §11.4).

Secrets must never reach config files, commit messages, logs, job records or the
vault. These patterns are a best-effort net for credential *shapes* — they do not
replace keeping secrets in environment variables only.
"""

from __future__ import annotations

import re

_PLACEHOLDER = "[redacted]"

#: Credential-shaped patterns.
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"[Bb]earer\s+[A-Za-z0-9._~+/-]{20,}"),
)


def contains_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def redact(text: str) -> str:
    """Replace every credential-shaped match with ``[redacted]``."""
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(_PLACEHOLDER, text)
    return text


__all__ = ["SECRET_PATTERNS", "contains_secret", "redact"]
