"""Observability: structured stage logs and opt-in LLM call logging (spec §12.4, §7.11).

Answers "what is an ingest costing me" (per-stage timings + per-role token counts
already land on the job record) and makes prompt regressions debuggable. **LLM
prompt/response logging is off by default** — prompts contain ingested content.

Everything written here — a log field or an LLM log line — is passed through
:func:`groundtruth.redaction.redact`; no log or job record ever contains a
secret (invariant 6). A logging failure never propagates: it must not fail a job.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .redaction import redact

STAGE_LOGGER = logging.getLogger("groundtruth.jobs")


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def log_stage(job_id: str, vault: str, stage: str, status: str, **extra: Any) -> None:
    """Emit one structured record for a job stage transition."""
    payload = _clean({"job_id": job_id, "vault": vault, "stage": stage, "status": status, **extra})
    STAGE_LOGGER.info("job stage %s -> %s", stage, status, extra={"groundtruth": payload})


class LLMCallLog:
    """Appends prompt/response records to ``<state-dir>/llm/<job-id>.jsonl`` when enabled."""

    def __init__(self, state_dir: Path | str, *, enabled: bool = False) -> None:
        self.enabled = enabled
        self._dir = Path(state_dir) / "llm"

    def record(
        self,
        job_id: str,
        *,
        role: str,
        model: str,
        prompt: Any,
        response_text: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        if not self.enabled:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            line = json.dumps(
                _clean(
                    {
                        "role": role,
                        "model": model,
                        "prompt": prompt,
                        "response": response_text,
                        "tool_calls": tool_calls or [],
                        "usage": usage or {},
                    }
                )
            )
            with (self._dir / f"{job_id}.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:  # logging must never fail a job
            STAGE_LOGGER.warning("LLM call log write failed for job %s", job_id, exc_info=True)


class LoggingLLMClient:
    """Wraps an LLM client, recording every ``complete`` call to an :class:`LLMCallLog`."""

    def __init__(self, inner: Any, call_log: LLMCallLog, job_id: str) -> None:
        self._inner = inner
        self._log = call_log
        self._job_id = job_id

    def complete(self, role: str, messages: Any, **kwargs: Any) -> Any:
        response = self._inner.complete(role, messages, **kwargs)
        self._log.record(
            self._job_id,
            role=role,
            model=getattr(response, "model", ""),
            prompt=list(messages),
            response_text=getattr(response, "text", None),
            tool_calls=[
                {"name": tc.name, "arguments": tc.arguments}
                for tc in getattr(response, "tool_calls", []) or []
            ],
            usage=_usage_dict(getattr(response, "usage", None)),
        )
        return response


def _usage_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0),
        "completion_tokens": getattr(usage, "completion_tokens", 0),
        "total_tokens": getattr(usage, "total_tokens", 0),
    }


__all__ = ["STAGE_LOGGER", "LLMCallLog", "LoggingLLMClient", "log_stage"]
