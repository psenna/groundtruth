from __future__ import annotations

from typing import Any

#: Level 3 of the precedence chain (spec §11.1): built-in defaults, the values
#: used when neither the global config nor a per-vault file specifies a key.
#: Mirrors the ``defaults:`` block of §11.2 plus the agent-loop limits of §8.2.
BUILTIN_DEFAULTS: dict[str, Any] = {
    "raw_archive": True,
    "auto_push": False,
    "allow_schema_writes": False,
    "models": {
        "default": {
            "base_url": "http://localhost:11434/v1",
            "model": "qwen2.5:14b",
            "api_key_env": "GT_API_KEY",
        },
        "tag": {"model": "qwen2.5:7b"},
        "reduce": {"model": "qwen2.5:14b"},
        "answer": {"model": "qwen2.5:32b"},
    },
    "limits": {
        "max_notes_per_ingest": 10,
        "max_note_bytes": 65536,
        "max_tool_calls": 30,
        "max_wall_clock_s": 60,
        "grep_max_matches": 50,
        "grep_max_bytes": 65536,
        "read_max_bytes": 32768,
        "vocab_max_bytes": 4096,
        "organize_max_attempts": 2,
    },
}
