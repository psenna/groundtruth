from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field


class ConfigError(ValueError):
    """A configuration file is malformed, missing a required value, or carries a secret."""


class ModelConfig(BaseModel):
    """Resolved LLM settings for one role (spec §4.3, §11.2)."""

    model_config = ConfigDict(extra="forbid")

    base_url: str
    model: str
    #: Name of the environment variable holding the API key — never the key itself (§11.4).
    api_key_env: str
    #: Reasoning/thinking effort, sent verbatim as ``reasoning_effort`` on the
    #: chat-completions request when set. OpenAI accepts ``minimal``/``low``/
    #: ``medium``/``high``; Ollama maps ``none`` to "thinking off" for models
    #: like Qwen3. Leave unset to send nothing (the backend's own default).
    reasoning_effort: str | None = None

    def resolve_api_key(self, environ: Mapping[str, str]) -> str | None:
        """Look up the API key in ``environ`` at use time (spec §11.4)."""
        return environ.get(self.api_key_env)


class Limits(BaseModel):
    """Resolved ingest and agent-loop limits (spec §8.2, §11.2)."""

    model_config = ConfigDict(extra="forbid")

    max_notes_per_ingest: int
    max_note_bytes: int
    max_tool_calls: int
    max_wall_clock_s: int
    grep_max_matches: int
    grep_max_bytes: int
    read_max_bytes: int
    vocab_max_bytes: int
    #: How many times the organize stage may run, re-prompted with the validator's
    #: rejection, before the job fails (spec §7.5 — "one pass or several"). ``1``
    #: disables the retry. Nothing is ever silently repaired: the final attempt
    #: still passes through the validator and the job fails loudly if it is
    #: invalid (ADR-5).
    organize_max_attempts: int = Field(default=2, ge=1)


class VaultConfig(BaseModel):
    """A fully-resolved, typed config for one vault (spec §11.1)."""

    model_config = ConfigDict(extra="forbid")

    raw_archive: bool
    auto_push: bool
    allow_schema_writes: bool
    #: Keyed by role: ``default``, ``tag``, ``reduce``, ``answer``.
    models: dict[str, ModelConfig]
    limits: Limits


class ServerConfig(BaseModel):
    """The ``server:`` block of ``config.yaml`` (spec §11.2)."""

    model_config = ConfigDict(extra="forbid")

    bind: str = "127.0.0.1"
    port: int = 8000
    auth: str = "none"
    mcp_endpoint: str = "/mcp"
    bearer_token_env: str | None = None


class GlobalConfig(BaseModel):
    """The parts of ``config.yaml`` the server process needs at startup (spec §11.2)."""

    model_config = ConfigDict(extra="ignore")

    server: ServerConfig = ServerConfig()
    state_dir: str
    job_retention_days: int = 7
    #: Registry seed: ``name -> repo root``.
    vaults: dict[str, str] = {}
    llm_logging: bool = False
    #: HTTP read/connect timeout (seconds) for every LLM call. The default suits a
    #: hosted API; raise it for a large local model where prompt processing of a
    #: big context can exceed a minute (a timed-out call is retried, so a job that
    #: hits this consistently fails after ~3x this value).
    llm_timeout_s: float = Field(default=60.0, gt=0)
