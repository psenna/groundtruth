#!/usr/bin/env bash
# Create milestones, labels and the 41 implementation issues for groundtruth.
#
# Requires a GitHub token with **Issues: read & write** on the repo.
# A fine-grained PAT without that permission fails with HTTP 403.
#
#   gh auth status                 # confirm which token is active
#   ./scripts/bootstrap-issues.sh  # idempotent: re-runs skip what exists
#
# Issue numbers assume a repo with no prior issues, so #1..#41 match the
# "Blocked by" references below. If issues already exist, dependency numbers
# in the bodies will be off — check before relying on them.

set -euo pipefail
REPO="${REPO:-psenna/groundtruth}"
EXISTING="$(mktemp)"
trap 'rm -f "$EXISTING"' EXIT

echo "==> repo: $REPO"
gh issue list -R "$REPO" --state all --limit 300 --json title --jq '.[].title' > "$EXISTING" 2>/dev/null || true

ensure_milestone() {
  local title="$1" desc="$2"
  if gh api "repos/$REPO/milestones?state=all" --jq '.[].title' 2>/dev/null | grep -Fxq "$title"; then
    echo "    = milestone: $title"
  else
    gh api -X POST "repos/$REPO/milestones" -f title="$title" -f description="$desc" --jq '.title' \
      | sed 's/^/    + milestone: /'
  fi
}

ensure_label() {
  local name="$1" color="$2" desc="$3"
  if gh label list -R "$REPO" --limit 200 --json name --jq '.[].name' 2>/dev/null | grep -Fxq "$name"; then
    echo "    = label: $name"
  else
    gh label create "$name" -R "$REPO" -c "$color" -d "$desc" >/dev/null && echo "    + label: $name"
  fi
}

# mkissue <title> <milestone> <comma-separated-labels>   ... body on stdin
mkissue() {
  local title="$1" milestone="$2" labels="$3"
  if grep -Fxq "$title" "$EXISTING"; then
    echo "    = #skip: $title"
    cat >/dev/null
    return
  fi
  local args=(-R "$REPO" -t "$title" -F - -m "$milestone")
  local IFS=','
  for l in $labels; do args+=(-l "$l"); done
  unset IFS
  gh issue create "${args[@]}" | sed 's|^|    + |'
}

echo "==> milestones"
ensure_milestone "M1 — Foundations"      "Scaffold, CI, domain models, config precedence, error taxonomy"
ensure_milestone "M2 — Storage layer"    "Notes, frontmatter, paths, git operations, source index, job store. No LLM."
ensure_milestone "M3 — Retrieval engine" "Shared agent loop, read-only tools, budget, LLM client (ADR-3)"
ensure_milestone "M4 — Ingestion"        "Dedup, write tools, validator, schema.md, prompts, pipeline orchestrator"
ensure_milestone "M5 — Recovery"         "Recovery agent, grounding check, refusals"
ensure_milestone "M6 — Jobs"             "Per-vault FIFO queue, retry policy, restart recovery"
ensure_milestone "M7 — API + auth"       "Auth layer and REST endpoints"
ensure_milestone "M8 — MCP + Web UI"     "MCP server with 8 tools; htmx web UI"
ensure_milestone "M9 — Eval + operations" "Golden eval, observability, Docker"

echo "==> labels"
ensure_label "area:storage"   "0e8a16" "Notes, git, indexes, job store"
ensure_label "area:llm"       "5319e7" "LLM client and prompts"
ensure_label "area:ingest"    "1d76db" "Ingestion pipeline"
ensure_label "area:recovery"  "b60205" "Recovery pipeline and grounding"
ensure_label "area:api"       "fbca04" "REST API and auth"
ensure_label "area:mcp"       "006b75" "MCP server"
ensure_label "area:web"       "c2e0c6" "Web UI"
ensure_label "area:ops"       "5a5a5a" "CI, Docker, observability"
ensure_label "security"       "d93f0b" "Security-critical: review with care"
ensure_label "spec-critical"  "e99695" "Enforces a core spec guarantee"

echo "==> issues"

# ---------------------------------------------------------------- M1
mkissue "Repo scaffold: uv, ruff, mypy, pytest" "M1 — Foundations" "area:ops" <<'BODY'
## Context
Establish the project skeleton so every later issue has a working test loop.
`pyproject.toml`, `.gitignore` and the package layout are already committed but have
**never been executed** — no `uv` was available on the authoring machine. This issue
verifies and corrects them, and produces the lockfile.

Spec: `docs/requirements.md` §4.1 · layout described in `CLAUDE.md`.

## Write these tests first
- [ ] `tests/unit/test_smoke.py::test_package_imports` — `import groundtruth` succeeds
- [ ] `tests/unit/test_smoke.py::test_version` — `groundtruth.__version__` matches pyproject

## Acceptance criteria
- [ ] `uv sync --all-extras` succeeds from a clean checkout
- [ ] `uv.lock` generated and committed
- [ ] `uv run pytest` green
- [ ] `uv run ruff check .` and `uv run ruff format --check .` clean
- [ ] `uv run mypy src/` clean under `strict = true`
- [ ] Any correction needed to `pyproject.toml` is made here

## Files
`pyproject.toml`, `uv.lock`, `src/groundtruth/__init__.py`, `tests/unit/test_smoke.py`

## Notes
First issue — nothing blocks it. Keep dependencies as they are; adding one needs approval.
BODY

mkissue "CI: lint, types and tests on push and PR" "M1 — Foundations" "area:ops" <<'BODY'
## Context
CI must fail on the same four checks a contributor runs locally, so "green locally" and
"green in CI" cannot diverge. A workflow exists at `.github/workflows/ci.yml` but has
never run.

Spec: `docs/requirements.md` §4.1.

## Write these tests first
No unit tests — verification is the workflow running green on a real PR.

## Acceptance criteria
- [ ] Workflow runs on push to `main` and on every PR
- [ ] Runs `ruff check`, `ruff format --check`, `mypy src/`, `pytest` — in that order
- [ ] Uses the committed `uv.lock` (no floating resolution)
- [ ] Demonstrated green on the PR that closes this issue
- [ ] A deliberately broken commit demonstrably fails CI (show it, then revert)

## Files
`.github/workflows/ci.yml`

## Notes
Blocked by #1 (needs `uv.lock`).
BODY

mkissue "Domain models: Vault, Note, Source, Job, Answer" "M1 — Foundations" "area:storage" <<'BODY'
## Context
The pydantic types every layer shares. Getting these right early prevents each subsystem
from inventing its own shape.

Spec: §5 (layout), §6 (note format), §8.4 (refusals), §12.1 (job records).

## Write these tests first
- [ ] `NoteFrontmatter` requires `title`, `tags`, `sources`, `created`, `updated`
- [ ] Tag validation rejects uppercase, spaces and underscores — normalized form is
      lowercase with `-` separators (§5.3)
- [ ] `sources` accepts a list of SHA-256 hex strings and rejects anything else
- [ ] `JobRecord` state transitions are constrained to the legal set
- [ ] `AnswerResult` and `Refusal` are distinguishable types; `Refusal` carries a reason of
      `no_evidence` or `budget_exhausted` (§8.4)

## Acceptance criteria
- [ ] Models for: `Vault`, `NoteFrontmatter`, `Note`, `SourceRecord`, `JobRecord`,
      `AnswerResult`, `Refusal`
- [ ] Invalid input raises `ValidationError` with a useful message
- [ ] `mypy --strict` clean

## Files
`src/groundtruth/models/`, `tests/unit/test_models.py`

## Notes
Blocked by #1. A refusal is a first-class result, not an error — model it as such.
BODY

mkissue "Config loading with 3-level precedence merge" "M1 — Foundations" "area:ops" <<'BODY'
## Context
Per-vault settings override global defaults override built-ins. Revision 1 of the spec
specified settings in two places with no merge rule; this is the resolution, and the
precedence is **documented behavior that must be tested**.

Spec: §11.1 (precedence), §11.2 (global), §11.3 (per-vault), §11.4 (secrets).

## Write these tests first
- [ ] Precedence: per-vault `.groundtruth.yaml` > global `config.yaml` > built-in defaults
- [ ] **Merge is per-key, not per-section** — a vault overriding `models.answer` still
      inherits `models.tag` from global
- [ ] Global config path resolves from `--config`, then `$GT_CONFIG`, then
      `/etc/groundtruth/config.yaml`
- [ ] `api_key_env` resolves the value from the environment at use time
- [ ] **A literal secret in a config file is rejected**, not silently accepted (§11.4)
- [ ] Missing per-vault file is fine; missing global file is fine; both missing yields
      built-in defaults

## Acceptance criteria
- [ ] Loader returns a fully-resolved, typed config for a given vault
- [ ] Precedence order is covered by explicit tests naming each level
- [ ] Config objects never carry secret *values* — only env var names

## Files
`src/groundtruth/config/`, `tests/unit/test_config.py`

## Notes
Blocked by #3. Invariant 6: secrets are env vars only.
BODY

mkissue "Error taxonomy: transient vs terminal" "M1 — Foundations" "area:ops" <<'BODY'
## Context
The retry policy (§12.2) depends on classifying failures. Encoding it as an exception
hierarchy now means every later subsystem raises something already classified, instead of
retry logic pattern-matching on strings.

Spec: §12.2.

## Write these tests first
- [ ] `TransientError` covers: connection refused, HTTP 429/502/503, read timeout
- [ ] `TerminalError` covers: validator rejection, git conflict / non-fast-forward,
      dirty working tree, malformed LLM output
- [ ] `is_transient(exc)` returns the right answer for every subclass
- [ ] An unrecognized exception is treated as **terminal**, not transient — failing loudly
      beats retrying blindly

## Acceptance criteria
- [ ] Exception hierarchy rooted at a `GroundtruthError`
- [ ] Every error carries the pipeline stage it occurred in (§7.11)
- [ ] Classification is a pure function, unit-tested without any I/O

## Files
`src/groundtruth/errors.py`, `tests/unit/test_errors.py`

## Notes
Blocked by #1. Used by #14 (LLM retry) and #28 (job retry policy).
BODY

# ---------------------------------------------------------------- M2
mkissue "Frontmatter parse and render roundtrip" "M2 — Storage layer" "area:storage" <<'BODY'
## Context
Note frontmatter is the index format — retrieval greps these files, so the structure is
normative, not cosmetic.

Spec: §6.

## Write these tests first
- [ ] Roundtrip: parse → render → parse produces an identical model
- [ ] Rendering is **stable** — same input yields byte-identical output (no key reordering,
      no dict churn), otherwise every ingest creates spurious diffs
- [ ] Body content with `---` inside it does not break parsing
- [ ] Missing frontmatter raises a clear error
- [ ] Malformed YAML raises a clear error naming the file
- [ ] Unicode in titles and bodies survives the roundtrip
- [ ] `sources` list order is preserved (append-only, §6)

## Acceptance criteria
- [ ] `parse_note(text) -> Note` and `render_note(note) -> str`
- [ ] Output is Obsidian-readable: `tags` in frontmatter as a YAML list
- [ ] Stability covered by a test, not by inspection

## Files
`src/groundtruth/storage/frontmatter.py`, `tests/unit/test_frontmatter.py`

## Notes
Blocked by #3. Unstable rendering causes noisy git history — treat it as a bug.
BODY

mkissue "Filename sanitization and vault path containment" "M2 — Storage layer" "area:storage,security" <<'BODY'
## Context
**Security-critical.** The LLM supplies note titles and folder names. Nothing derived from
model output may resolve outside the vault directory. This is the boundary that keeps an
LLM from becoming an arbitrary write primitive.

Spec: §7.6. Invariant 2 in `CLAUDE.md`.

## Write these tests first
- [ ] Traversal rejected: `../`, `..\\`, absolute paths, `~` expansion
- [ ] Rejected: path separators in a title, null bytes, leading dots
- [ ] Rejected: symlink escape — a folder that is a symlink pointing outside the vault
- [ ] Rejected: Windows reserved names (`CON`, `NUL`, `AUX`, …) and trailing dots/spaces
- [ ] Unicode titles are preserved where safe, normalized (NFC) where not
- [ ] Overlong titles truncated deterministically without collision
- [ ] **Property test (hypothesis): for arbitrary input strings, the resolved path is always
      inside the vault root — or the call raises.** There is no third outcome.

## Acceptance criteria
- [ ] `sanitize_title(str) -> str` and `resolve_in_vault(vault_root, *parts) -> Path`
- [ ] Containment verified after full resolution, including symlinks (`Path.resolve()`)
- [ ] Every rejection raises `TerminalError`, never returns a fallback path
- [ ] Property test present and passing

## Files
`src/groundtruth/storage/paths.py`, `tests/unit/test_paths.py`

## Notes
Blocked by #3. Review this one carefully — sanitize-and-continue was explicitly rejected
in ADR-5. Reject, never repair.
BODY

mkissue "Note repository: read, write, list, tags" "M2 — Storage layer" "area:storage" <<'BODY'
## Context
The filesystem layer for notes. Every read and write of a note goes through here, so the
containment guarantee from #7 has exactly one enforcement point.

Spec: §5, §6.

## Write these tests first
- [ ] Write then read returns an identical note
- [ ] `list_notes()` walks the vault and skips `schema.md` and non-Markdown files
- [ ] `list_notes(tag=...)` filters on frontmatter tags
- [ ] Writing to a path outside the vault raises (delegates to #7)
- [ ] Reading a nonexistent note raises a clear, named error
- [ ] `updated` timestamp is refreshed on write; `created` is preserved
- [ ] Appending to `sources` does not duplicate an existing hash

## Acceptance criteria
- [ ] `NoteRepository` bound to one vault root
- [ ] All paths pass through `resolve_in_vault` from #7 — no direct `Path` joins
- [ ] Tests use `tmp_path`; no mocking of the filesystem

## Files
`src/groundtruth/storage/notes.py`, `tests/unit/test_notes.py`

## Notes
Blocked by #6, #7.
BODY

mkissue "Git operations wrapper" "M2 — Storage layer" "area:storage,spec-critical" <<'BODY'
## Context
Git is the versioning layer, the sync mechanism and the undo mechanism. The
dirty-tree check and the rollback are a **pair** — the rollback (`reset --hard` +
`clean -fd`) is only safe because the tree was verified clean at job start. Implementing
one without the other destroys user data.

Spec: §7.1, §7.3, §7.7, §7.9, §7.10 · ADR-4.

## Write these tests first
Use real temp repos throughout — do not mock git.
- [ ] `is_clean()` true on a clean tree; false with unstaged, staged, or untracked changes
- [ ] `commit()` uses the dedicated `groundtruth` identity, **not** the operator's config
- [ ] `rollback()` removes both modified and newly created untracked files
- [ ] `rollback()` on a repo that was clean at start restores it exactly
- [ ] `pull_ff_only()` succeeds on a fast-forward; raises `TerminalError` on divergence
- [ ] `push()` failure raises a *distinguishable* error — the local commit stays valid (§7.10)
- [ ] Commit message format matches §7.9

## Acceptance criteria
- [ ] Operations: `is_clean`, `add`, `commit`, `rollback`, `pull_ff_only`, `push`, `head_sha`
- [ ] Errors classified per #5 (conflict = terminal, network = transient)
- [ ] Git identity configured per-invocation, never by mutating global git config

## Files
`src/groundtruth/storage/git.py`, `tests/unit/test_git.py`

## Notes
Blocked by #5. Invariants 5 and 7. Ship `is_clean` and `rollback` together in one PR.
BODY

mkissue "Source index store in the state dir" "M2 — Storage layer" "area:storage" <<'BODY'
## Context
Maps source SHA-256 → job id, commit sha, notes touched, ingested_at. It lives in the
**state dir, not in `external/`** (ADR-7) precisely so that dedup and note provenance keep
working when `raw_archive` is disabled.

Spec: §5.1 · ADR-7.

## Write these tests first
- [ ] Put then get returns the record
- [ ] Unknown hash returns `None` — no exception
- [ ] Index is per-vault: the same hash in two vaults is two independent entries
- [ ] Writes are atomic — a crash mid-write (simulate: write to temp + rename) never leaves
      a truncated or unparseable index
- [ ] Concurrent writes from two processes do not lose an entry
- [ ] `remove(sha)` deletes the entry (used by retraction, §12.3)

## Acceptance criteria
- [ ] `SourceIndex` keyed by vault, persisted at `<state-dir>/index/<vault>.json`
- [ ] Atomic write via temp file + `os.replace`
- [ ] Works identically regardless of the `raw_archive` setting

## Files
`src/groundtruth/storage/source_index.py`, `tests/unit/test_source_index.py`

## Notes
Blocked by #3. Never write this into a vault repo — it is state, not content.
BODY

mkissue "Job store with retention sweep" "M2 — Storage layer" "area:storage" <<'BODY'
## Context
Job records persist as JSON in the state dir and must survive a restart.

Spec: §4.4, §12.1.

## Write these tests first
- [ ] Create, update and load a job record by id
- [ ] Records survive a simulated restart (new store instance, same directory)
- [ ] Legal state transitions accepted; illegal ones rejected
- [ ] Retention sweep deletes records older than `job_retention_days`, keeps newer ones
- [ ] Sweep never deletes a job in a non-terminal state, regardless of age
- [ ] Job records contain **no secrets** (§11.4) — assert this explicitly
- [ ] Atomic writes, as in #10

## Acceptance criteria
- [ ] `JobStore` over `<state-dir>/jobs/<job-id>.json`
- [ ] `sweep()` callable on startup and on a schedule
- [ ] Records carry per-stage timings and token counts (populated later by #40)

## Files
`src/groundtruth/storage/job_store.py`, `tests/unit/test_job_store.py`

## Notes
Blocked by #3, #5. Invariant 6.
BODY

# ---------------------------------------------------------------- M3
mkissue "Budget tracker for agent loops" "M3 — Retrieval engine" "area:llm,spec-critical" <<'BODY'
## Context
Bounds the shared agent loop. Exhaustion is not an error — it produces a **refusal**,
structurally identical to no-evidence (ADR-6). This is what keeps §8.4 absolute.

Spec: §8.2 · ADR-6.

## Write these tests first
- [ ] Tool-call ceiling reached → exhaustion signalled
- [ ] Wall-clock ceiling reached → exhaustion signalled (inject a clock; do not sleep)
- [ ] Byte caps: `grep_max_matches`, `grep_max_bytes`, `read_max_bytes` each enforced
- [ ] Truncation is **flagged**, never silent — the caller can tell output was cut
- [ ] Defaults match §8.2 exactly (30 calls, 60 s, 50 matches, 64 KB, 32 KB)
- [ ] Per-vault overrides take effect
- [ ] Budget is not shared across concurrent agent runs

## Acceptance criteria
- [ ] `Budget` tracks consumption and reports `exhausted` with the limit that tripped
- [ ] Time is injectable so tests are fast and deterministic
- [ ] Exhaustion never raises past the loop — it is a normal, expected outcome

## Files
`src/groundtruth/retrieval/budget.py`, `tests/unit/test_budget.py`

## Notes
Blocked by #5. Invariant 4.
BODY

mkissue "Read-only tools: ls, grep, read" "M3 — Retrieval engine" "area:llm,security" <<'BODY'
## Context
The only vault access an agent gets. Shared by both pipelines (ADR-3). **These tools must
be incapable of writing** — that is invariant 1, enforced here rather than by convention.

Spec: §8.1, §8.2.

## Write these tests first
- [ ] `ls` lists a directory inside the vault; refuses a path outside it
- [ ] `grep` returns matches with note paths and line numbers
- [ ] `grep` truncates at `grep_max_matches` / `grep_max_bytes` and says that it did
- [ ] `read` returns content, truncating at `read_max_bytes` with an explicit marker
- [ ] Every tool decrements the budget from #12
- [ ] Once the budget is exhausted, further calls refuse rather than execute
- [ ] **No tool exposes a write, move, or delete** — assert the exposed tool surface
- [ ] Path arguments route through `resolve_in_vault` (#7); traversal is refused

## Acceptance criteria
- [ ] Three tools with JSON-schema definitions suitable for an LLM tool call
- [ ] Output is size-bounded and truncation is always visible to the model
- [ ] A test asserts the tool set contains exactly these three

## Files
`src/groundtruth/retrieval/tools.py`, `tests/unit/test_tools.py`

## Notes
Blocked by #8, #12. Invariant 1.
BODY

mkissue "OpenAI-compatible LLM client with per-role models" "M3 — Retrieval engine" "area:llm" <<'BODY'
## Context
One client for OpenAI, Ollama, vLLM — anything exposing the compatible API. Models are
configurable per role (`tag`, `reduce`, `answer`) over a shared default.

Spec: §4.3, §11.2 · retry classification from §12.2.

## Write these tests first
Fake the HTTP layer; no network in unit tests.
- [ ] Role resolution: `tag` uses its own model, inherits `base_url` and `api_key_env`
      from `default`
- [ ] A role with no override falls back to `default`
- [ ] API key read from the env var **at call time**, never stored on the object
- [ ] Transient failures (429, 502, 503, timeout, connection refused) retry twice with
      backoff, then raise
- [ ] Terminal failures (400, malformed response) raise immediately, no retry
- [ ] Backoff is injectable — tests must not actually sleep
- [ ] Tool-call responses parse correctly; malformed output raises `TerminalError`
- [ ] Token usage is captured per call (consumed later by #40)

## Acceptance criteria
- [ ] `LLMClient.complete(role, messages, tools=...)`
- [ ] Retry policy delegates classification to #5 — no duplicate error matching
- [ ] The API key never appears in logs, exceptions, or `repr()` — assert this

## Files
`src/groundtruth/llm/client.py`, `tests/unit/test_llm_client.py`

## Notes
Blocked by #4, #5. Invariant 6.
BODY

mkissue "Agent loop harness" "M3 — Retrieval engine" "area:llm" <<'BODY'
## Context
The shared loop both pipelines run: dispatch tool calls, accumulate a transcript, enforce
the budget, decide termination. Ingestion and recovery differ only in prompt and tool set
(ADR-3) — do not fork this.

Spec: §7.4, §8.

## Write these tests first
With a scripted fake LLM client:
- [ ] Loop runs until the model stops requesting tools, then returns the final message
- [ ] Each tool call decrements the budget; exhaustion terminates the loop cleanly with an
      `exhausted` outcome rather than an exception
- [ ] An unknown tool name is reported back to the model, not crashed on
- [ ] A tool raising is reported to the model as a tool error; the loop continues
- [ ] Transcript records every call and result, in order
- [ ] The loop is generic over the tool set — a read-only set and a read+write set both run

## Acceptance criteria
- [ ] `run_agent(client, role, system, tools, budget) -> AgentOutcome`
- [ ] `AgentOutcome` distinguishes completed / exhausted / failed
- [ ] Zero vault-specific or pipeline-specific logic in this module

## Files
`src/groundtruth/retrieval/agent.py`, `tests/unit/test_agent.py`

## Notes
Blocked by #13, #14. This is used by #21 (ingest) and #24 (recovery).
BODY

# ---------------------------------------------------------------- M4
mkissue "Dedup: SHA-256 lookup and short-circuit" "M4 — Ingestion" "area:ingest" <<'BODY'
## Context
Exact-hash dedup against the source index. A hit skips all LLM work and returns the prior
result — the cheapest possible path through the pipeline.

Spec: §7.2 · non-goal: near-duplicate detection (§3.3).

## Write these tests first
- [ ] Identical text yields an identical hash
- [ ] A hash hit returns the prior notes and commit ref **without any LLM call** — assert
      the fake client was never invoked
- [ ] A miss proceeds normally
- [ ] Dedup is per-vault: the same text in a different vault is a miss
- [ ] Trailing-whitespace difference is a **miss** (exact-match only, documented limitation)
- [ ] Hashing is over decoded text, and is stable across platforms

## Acceptance criteria
- [ ] `content_hash(text) -> str` (SHA-256 hex)
- [ ] Dedup check queries the source index from #10
- [ ] Short-circuit result is distinguishable from a fresh ingest in the job record

## Files
`src/groundtruth/ingest/dedup.py`, `tests/unit/test_dedup.py`

## Notes
Blocked by #10.
BODY

mkissue "Write tools: create_note and update_note" "M4 — Ingestion" "area:ingest,security" <<'BODY'
## Context
The **only** way LLM output reaches disk. The model never emits a filesystem path — it
supplies a folder and a title, and the system derives the path. Constrain by construction
(ADR-5).

Spec: §7.6. Invariant 2.

## Write these tests first
- [ ] `create_note(folder, title, body)` — no path parameter exists in the signature
- [ ] `update_note(path, body)` refuses a path that does not already exist
- [ ] Writes are **buffered, not applied** — the tools stage intentions for the validator
      (#18) to gate; nothing touches disk during the agent loop
- [ ] Creating the same title twice in one job is detected
- [ ] Tool JSON schemas expose exactly these parameters and no others
- [ ] Frontmatter is constructed by the system, not accepted from the model — the model
      cannot set `sources` or timestamps itself

## Acceptance criteria
- [ ] Two tools, LLM-callable, returning success/failure to the model
- [ ] A `PendingWrites` collection the validator and committer consume
- [ ] No filesystem mutation anywhere in this module

## Files
`src/groundtruth/ingest/write_tools.py`, `tests/unit/test_write_tools.py`

## Notes
Blocked by #8. Buffering is what makes all-or-nothing (§7.7) achievable — do not write
through directly.
BODY

mkissue "Write validator: gate every staged change" "M4 — Ingestion" "area:ingest,security,spec-critical" <<'BODY'
## Context
The gate between model output and the repository. **Any violation fails the whole job with
nothing staged** — sanitize-and-continue was explicitly rejected (ADR-5) because it erodes
vault quality silently.

Spec: §7.6. Invariant 2.

## Write these tests first
One test per rule in the §7.6 table:
- [ ] Folder not listed in `schema.md` → reject
- [ ] Title producing an unsafe filename → reject (delegates to #7)
- [ ] Resolved path outside the vault → reject
- [ ] More than `max_notes_per_ingest` notes touched → reject (default 10)
- [ ] Note larger than `max_note_bytes` → reject (default 64 KB)
- [ ] Missing or unparseable frontmatter → reject
- [ ] Frontmatter missing a required key → reject
- [ ] **A valid batch passes and stages everything atomically**
- [ ] Rejection reports *which* rule failed and on which note
- [ ] Partial validity → whole batch rejected, nothing staged

## Acceptance criteria
- [ ] `validate(pending_writes, schema, limits) -> None | raises TerminalError`
- [ ] Limits come from config (#4), not hardcoded
- [ ] No repair, no normalization-and-continue — reject only

## Files
`src/groundtruth/ingest/validator.py`, `tests/unit/test_validator.py`

## Notes
Blocked by #7, #17, #20. Highest-value security review in the project.
BODY

mkissue "Wikilink extraction and link-integrity check" "M4 — Ingestion" "area:ingest" <<'BODY'
## Context
Every `[[link]]` must resolve to a note that exists, or to one created in the same job.
Dangling links break navigation and, worse, produce citations pointing at nothing.

Spec: §7.6 (link integrity), §6 (body format).

## Write these tests first
- [ ] Extract `[[Simple]]`, `[[folder/Note]]`, `[[Note|display text]]`
- [ ] Ignore `[[...]]` inside fenced code blocks and inline code
- [ ] A link to an existing note resolves
- [ ] A link to a note created **in the same job** resolves (order-independent)
- [ ] A link to a nonexistent note is reported as dangling
- [ ] Escaped or malformed brackets do not crash the parser
- [ ] Unicode note names resolve

## Acceptance criteria
- [ ] `extract_links(body) -> list[Link]` and
      `check_links(links, existing, created_this_job) -> list[Dangling]`
- [ ] Used by the validator (#18) and by the grounding check (#25)

## Files
`src/groundtruth/ingest/links.py`, `tests/unit/test_links.py`

## Notes
Blocked by #8. Reused by recovery — keep it free of ingest-specific assumptions.
BODY

mkissue "schema.md parser and gated append" "M4 — Ingestion" "area:ingest,spec-critical" <<'BODY'
## Context
`schema.md` is the user-authored map of a vault. The system may append to it — recording
new tags — **only when `schema_evolution` is enabled**, and that gate applies at every
entry point including the MCP `update_schema` tool.

Spec: §5.2, §5.3, §13.1.

## Write these tests first
- [ ] Parse the declared folder list and tag vocabulary from the template format
- [ ] Parsing tolerates user prose, extra sections and reordering — it is a human document
- [ ] `schema_evolution: false` → **every** append attempt refuses
- [ ] `schema_evolution: true` → a new tag is appended
- [ ] Appending **preserves user prose and structure** — assert unrelated content is
      byte-identical afterwards
- [ ] A tag that already exists is not appended twice
- [ ] Appended tags are normalized (lowercase, dash-separated)
- [ ] A malformed `schema.md` raises a clear error naming the problem

## Acceptance criteria
- [ ] `parse_schema(text) -> Schema` with `.folders` and `.tags`
- [ ] `append_tags(schema_text, tags, allowed: bool) -> str` — refuses when not allowed
- [ ] The gate is enforced *in this module*, so no caller can bypass it

## Files
`src/groundtruth/ingest/schema.py`, `tests/unit/test_schema.py`

## Notes
Blocked by #6. See spec open question 4 — the append mechanism (anchored section vs.
fenced block) is unresolved. Pick one, document it in the PR, and keep user prose intact.
BODY

mkissue "Ingestion prompts: tag, reduce, organize" "M4 — Ingestion" "area:llm" <<'BODY'
## Context
The reduce step is the product. Everything else in this system is mechanically verifiable;
whether distillation produces a vault worth querying is a prompt-iteration problem
(spec open question 3). **Expect several passes — that is normal, not failure.**

Spec: §7.5 · granularity per ADR-9.

## Write these tests first
Structural assertions with a scripted fake client — do not assert on prose quality.
- [ ] Prompts include `schema.md` content as the organizing map
- [ ] Tag output is normalized (lowercase, dash-separated) or rejected
- [ ] **Granularity: one note per topic/entity**, not per claim (ADR-9) — assert the model
      is instructed accordingly and that a multi-fact input maps to few notes
- [ ] Organize output only ever calls `create_note` / `update_note` (#17)
- [ ] Conflicting information: newer value replaces older (ADR-8)
- [ ] Malformed model output raises `TerminalError` rather than being coerced

## Acceptance criteria
- [ ] Prompts for the three roles, versioned in-repo as files (not inline strings)
- [ ] The reduce prompt states explicit criteria: keep claims, facts and relationships;
      discard narration, hedging and restatement (§7.5)
- [ ] A documented way to iterate: run against the fixture vault and eyeball the diff

## Files
`src/groundtruth/ingest/prompts/`, `tests/unit/test_ingest_prompts.py`

## Notes
Blocked by #15. Do not chase quality here before #39's eval exists to measure it.
BODY

mkissue "Raw archive writer and commit message formatter" "M4 — Ingestion" "area:ingest" <<'BODY'
## Context
The raw archive preserves the original text so a claim can be re-read at its source. It is
optional; dedup and provenance do not depend on it (ADR-7).

Spec: §7.8 (archive), §7.9 (commit message).

## Write these tests first
- [ ] `raw_archive: true` writes `external/<sha>.txt` and `external/<sha>.json`
- [ ] `raw_archive: false` writes neither — but the source index (#10) is still updated
- [ ] The `.txt` is **immutable**: re-ingesting the same hash never rewrites it
- [ ] Manifest contains hash, `ingested_at`, source label, job id, commit sha, notes touched
- [ ] `external/` is inside the repo but outside the vault (§5)
- [ ] Commit message matches the §7.9 format: action, vault, notes, tags, source hash, excerpt
- [ ] The excerpt is truncated and **contains no secrets** (§11.4)

## Acceptance criteria
- [ ] Archive writer honoring the per-vault flag
- [ ] Commit message formatter as a pure, unit-tested function
- [ ] Commit sha is written into the manifest after the commit exists

## Files
`src/groundtruth/ingest/archive.py`, `src/groundtruth/ingest/commit_message.py`, tests

## Notes
Blocked by #9, #10. Invariant 6.
BODY

mkissue "Ingest pipeline orchestrator" "M4 — Ingestion" "area:ingest,spec-critical" <<'BODY'
## Context
Assembles the whole ingestion sequence with all-or-nothing semantics. This is where the
atomicity guarantee is either real or a lie — revision 1 of the spec claimed it without the
dirty-tree precondition that makes it achievable (ADR-4).

Spec: §7 end to end.

## Write these tests first
Integration tests over real temp repos with a scripted fake LLM.
- [ ] **Dirty working tree → job fails immediately, nothing changed** (§7.1)
- [ ] Dedup hit → short-circuits, no LLM call, no commit
- [ ] `pull --ff-only` conflict → abort, no changes
- [ ] Validator rejection → rollback, **nothing staged, nothing committed**
- [ ] LLM failure mid-run → rollback, repo byte-identical to its pre-job state
- [ ] Happy path → exactly **one** commit containing notes + archive together
- [ ] Push failure → the local commit survives and the job still reports success (§7.10)
- [ ] Every failure path leaves the repo clean — assert `is_clean()` after each
- [ ] Job record populated per §7.11

## Acceptance criteria
- [ ] Stage order exactly as §7: clean-tree → dedup → pull → retrieve → LLM → validate →
      archive → stage → commit → push → result
- [ ] Rollback on failure at every stage, verified individually
- [ ] Orchestration only — no business logic that belongs in #16–#22

## Files
`src/groundtruth/ingest/pipeline.py`, `tests/integration/test_ingest_pipeline.py`

## Notes
Blocked by #16, #18, #19, #21, #22. Invariants 5 and 7. The largest issue in M4 — if it
grows further, split the failure-path tests into their own PR.
BODY

# ---------------------------------------------------------------- M5
mkissue "Recovery agent orchestration" "M5 — Recovery" "area:recovery,spec-critical" <<'BODY'
## Context
Query in, grounded answer out. The agent reads `schema.md` first as the map, then searches
with read-only tools. **Recovery never writes** — no note, no `schema.md`, no commit.

Spec: §8.1, §8.2, §8.5.

## Write these tests first
With a fixture vault and a scripted fake client:
- [ ] `schema.md` is read before any search tool call
- [ ] Only the three read-only tools from #13 are exposed — assert the tool set
- [ ] **The vault is byte-identical after a query** — assert this on every recovery test
- [ ] Budget exhaustion returns an `exhausted` outcome, not an exception
- [ ] Every call takes an explicit vault; nothing assumes a single vault (§8.5)
- [ ] A query against an empty vault terminates promptly rather than looping

## Acceptance criteria
- [ ] `recover(vault, question) -> AgentOutcome` built on the shared loop (#15)
- [ ] No write tool is constructible from within this module
- [ ] Vault-unchanged assertion present in the test suite as a standing guard

## Files
`src/groundtruth/recovery/agent.py`, `tests/integration/test_recovery.py`

## Notes
Blocked by #15, #20. Invariant 1 — this is the issue where it is enforced.
BODY

mkissue "Grounding runtime check" "M5 — Recovery" "area:recovery,spec-critical" <<'BODY'
## Context
Layer 1 of grounding verification, applied to **every** answer before it is returned. It
catches fabricated note names — the cheapest and most common grounding failure. A failure
downgrades the answer to a refusal; it never softens it.

Spec: §9.1. Invariant 3.

## Write these tests first
- [ ] An answer with zero citations → **refusal**
- [ ] An answer citing a nonexistent note → **refusal**
- [ ] An answer whose citations all resolve → passes through unchanged
- [ ] A partially-valid answer (one good citation, one dangling) → **refusal**, not a
      filtered or repaired answer
- [ ] The check runs on every path — assert it cannot be bypassed by a caller
- [ ] Citation extraction reuses `extract_links` from #19, not a second parser

## Acceptance criteria
- [ ] `check_grounding(answer, vault) -> AnswerResult | Refusal`
- [ ] Downgrade is total: no hedged, annotated or partially-stripped answers exist
- [ ] Refusal carries `no_evidence`

## Files
`src/groundtruth/recovery/grounding.py`, `tests/unit/test_grounding.py`

## Notes
Blocked by #24. Invariant 3. Reviewers: reject any code path that returns a modified
answer instead of a refusal.
BODY

mkissue "Answer formatting and refusal types" "M5 — Recovery" "area:recovery" <<'BODY'
## Context
The user-facing shape of both outcomes. **Refusals are a first-class result, not an
error** — HTTP 200 with a structured outcome (§8.4). Budget exhaustion and no-evidence are
structurally identical by design (ADR-6).

Spec: §8.3, §8.4 · ADR-6.

## Write these tests first
- [ ] An answer renders as Markdown with `[[note]]` citations
- [ ] Every substantive claim carries a citation (§8.3)
- [ ] `Refusal(no_evidence)` and `Refusal(budget_exhausted)` are the only two reasons
- [ ] **The two refusals are structurally identical** — same type, same shape, differing
      only in `reason`
- [ ] The budget-exhausted message matches §8.2's wording
- [ ] No refusal path ever emits partial findings, a caveat, or a warning banner

## Acceptance criteria
- [ ] `AnswerResult` and `Refusal` rendered for API, MCP and web consumption
- [ ] A single formatting module — the three surfaces must not each invent their own
- [ ] A test asserts no code path produces a "partial answer"

## Files
`src/groundtruth/recovery/format.py`, `tests/unit/test_answer_format.py`

## Notes
Blocked by #25. Invariants 3 and 4.
BODY

# ---------------------------------------------------------------- M6
mkissue "Per-vault FIFO job queue and worker" "M6 — Jobs" "area:ops" <<'BODY'
## Context
One ingest at a time per vault — this serialization is what makes the git and filesystem
mutations safe. Different vaults run concurrently.

Spec: §4.4.

## Write these tests first
- [ ] Two jobs on the **same** vault run strictly in submission order, never overlapping
- [ ] Jobs on **different** vaults run concurrently
- [ ] A failing job does not block the queue — the next job proceeds
- [ ] `wait=true` blocks until a terminal state; `wait=false` returns a job id immediately
- [ ] Queue depth and position are observable
- [ ] Shutdown drains or cleanly abandons in-flight work without corrupting job records

## Acceptance criteria
- [ ] `JobQueue` with per-vault FIFO semantics and cross-vault parallelism
- [ ] Every state transition persisted through the job store (#11)
- [ ] Concurrency covered by tests that would actually fail if serialization broke

## Files
`src/groundtruth/jobs/queue.py`, `tests/integration/test_job_queue.py`

## Notes
Blocked by #11, #23. Serialization is a correctness requirement, not a performance choice.
BODY

mkissue "Retry policy: transient twice, terminal never" "M6 — Jobs" "area:ops" <<'BODY'
## Context
A local-first design leans on a model server that restarts and OOMs, so transient retry
earns its keep. Retrying a validator rejection does not — it fails identically the second
time and burns tokens.

Spec: §12.2.

## Write these tests first
- [ ] A transient failure retries up to **twice**, then fails
- [ ] Backoff increases between attempts; backoff is injectable so tests do not sleep
- [ ] Terminal failures (validator, git conflict, dirty tree, malformed output) **never**
      retry — assert exactly one attempt
- [ ] A job succeeding on retry 2 is recorded as successful, with the attempt count
- [ ] Retry attempts are visible in the job record
- [ ] Classification delegates to #5 — no second copy of the taxonomy

## Acceptance criteria
- [ ] Retry policy applied by the worker, not scattered through the pipeline
- [ ] Attempt counts and per-attempt errors persisted
- [ ] A terminal error surfaces its cause immediately, undelayed by backoff

## Files
`src/groundtruth/jobs/retry.py`, `tests/unit/test_retry.py`

## Notes
Blocked by #27.
BODY

mkissue "Restart recovery for in-flight jobs" "M6 — Jobs" "area:ops" <<'BODY'
## Context
Job state must survive a restart (§4.4). A job interrupted mid-ingest left its repo in
whatever state the crash produced — recovery must reconcile that honestly rather than
assume.

Spec: §4.4, §12.1.

## Write these tests first
- [ ] Queued jobs are re-queued on startup, preserving order
- [ ] A job interrupted mid-run is **not silently resumed** — it is marked failed with an
      explanatory reason, and the vault is rolled back to clean
- [ ] Terminal jobs are untouched by recovery
- [ ] Retention sweep (#11) runs on startup
- [ ] A corrupt job record is quarantined, not crashed on
- [ ] Recovery is idempotent — running it twice changes nothing the second time

## Acceptance criteria
- [ ] Startup routine reconciling the state dir with reality
- [ ] Interrupted jobs never resume mid-pipeline; all-or-nothing is preserved
- [ ] The vault is verified clean before the queue accepts new work

## Files
`src/groundtruth/jobs/recovery.py`, `tests/integration/test_restart_recovery.py`

## Notes
Blocked by #27. Invariant 7 — resuming a half-finished ingest would break it.
BODY

# ---------------------------------------------------------------- M7
mkissue "Auth layer: strategy protocol and registry" "M7 — API + auth" "area:api,security" <<'BODY'
## Context
One authentication layer shared by API, MCP and web, so the three cannot diverge. Ships
`none` (localhost default) and `bearer` (single static token). The registry is more
structure than two strategies need — accepted deliberately (ADR-11).

Spec: §4.5 · ADR-11.

## Write these tests first
- [ ] `none` resolves an anonymous principal
- [ ] `bearer` accepts the configured token and rejects a wrong or absent one
- [ ] Token comparison is **constant-time** — no early-exit string compare
- [ ] The token is read from an env var, never from a config file (§11.4)
- [ ] The token never appears in logs, error responses, or `repr()`
- [ ] Registry resolves a strategy by config name; an unknown name fails at startup, loudly
- [ ] A third strategy can be registered without modifying existing modules

## Acceptance criteria
- [ ] `AuthStrategy` protocol, registry, and the two implementations
- [ ] The core engine receives a resolved principal and never sees a request or header
- [ ] Default configuration is `none` bound to localhost

## Files
`src/groundtruth/auth/`, `tests/unit/test_auth.py`

## Notes
Blocked by #4. Invariant 6.
BODY

mkissue "FastAPI skeleton with refusal-aware error handling" "M7 — API + auth" "area:api,spec-critical" <<'BODY'
## Context
The app shell. The important decision: **a refusal is a successful response**, HTTP 200
with a structured outcome — not a 404 and not an error (§8.4).

Spec: §8.4, §10.1.

## Write these tests first
- [ ] A refusal returns **200** with a structured body carrying its reason
- [ ] A `TerminalError` maps to 4xx with a useful message
- [ ] A `TransientError` reaching the API maps to 503
- [ ] **No error response leaks a secret, a token, or an absolute host path** — assert it
- [ ] Auth dependency from #30 is wired and enforced
- [ ] Health endpoint requires no auth
- [ ] Request validation errors return 422 with field detail

## Acceptance criteria
- [ ] App factory, exception handlers, auth dependency, health endpoint
- [ ] Adapter only — no business logic in this layer (`CLAUDE.md` layer rule)
- [ ] Refusal shape identical to the one MCP and web will use (#26)

## Files
`src/groundtruth/api/app.py`, `tests/integration/test_api_app.py`

## Notes
Blocked by #30. A refusal returned as an error would misrepresent the product's core
behavior — reviewers should check this specifically.
BODY

mkissue "Vault endpoints: adopt, init, list, deregister" "M7 — API + auth" "area:api" <<'BODY'
## Context
Registration **adopts** an existing repo by default and **scaffolds** with `init: true`.
Deregistration never deletes files — the repo belongs to the user.

Spec: §13.1, §13.2, §10.1.

## Write these tests first
- [ ] Adopt validates: is a git repo, vault dir exists, `schema.md` exists, tree is clean
- [ ] Each validation failure returns 422 naming **the specific problem**
- [ ] `init: true` scaffolds: `git init`, layout per §5, starter `schema.md`, default
      `.groundtruth.yaml`, `.gitignore`, initial commit
- [ ] The starter `schema.md` matches the §13.1 template
- [ ] `init` on a non-empty directory refuses rather than overwriting
- [ ] `DELETE` removes the registry entry and source index but **leaves all files on disk**
      — assert the repo still exists afterwards
- [ ] Listing returns registered vaults with metadata

## Acceptance criteria
- [ ] Four endpoints per §10.1
- [ ] Adopt and init share validation where sensible but remain distinct paths
- [ ] Registry changes persisted

## Files
`src/groundtruth/api/vaults.py`, `src/groundtruth/storage/registry.py`, tests

## Notes
Blocked by #9, #20, #31. Deleting user data on deregistration would be a serious bug.
BODY

mkissue "Ingest and job endpoints" "M7 — API + auth" "area:api" <<'BODY'
## Context
Submit text for ingestion, poll job state. `wait=true` blocks until terminal.

Spec: §10.1, §4.4, §12.1.

## Write these tests first
- [ ] `POST /ingest` returns a job id immediately by default
- [ ] `wait=true` blocks and returns the completed result
- [ ] `wait=true` on a job that fails returns the failure, not a timeout
- [ ] A dedup hit returns the prior result with a distinguishable indicator
- [ ] `GET /jobs/{id}` returns the record; unknown id → 404
- [ ] Ingesting into an unregistered vault → 422
- [ ] Ingest while the vault tree is dirty → the job fails with that specific reason (§7.1)
- [ ] Job responses expose no secrets

## Acceptance criteria
- [ ] Endpoints per §10.1, delegating entirely to the queue (#27)
- [ ] `wait` has a bounded timeout that degrades to returning the job id
- [ ] Adapter only

## Files
`src/groundtruth/api/ingest.py`, `tests/integration/test_api_ingest.py`

## Notes
Blocked by #27, #31.
BODY

mkissue "Query, notes and schema read endpoints" "M7 — API + auth" "area:api" <<'BODY'
## Context
The read surface, including the endpoint the whole product exists for.

Spec: §10.1, §8, §9.1.

## Write these tests first
- [ ] `POST /query` returns an answer with citations for an answerable question
- [ ] `POST /query` returns **200 + refusal** for an unanswerable one
- [ ] Budget exhaustion returns a refusal with `budget_exhausted`
- [ ] **A query never modifies the vault** — assert byte-identical before and after
- [ ] `GET /notes` filters by path and tag; `GET /notes/{path}` returns content
- [ ] Note paths are containment-checked (#7) — traversal via the API is refused
- [ ] `GET /schema` returns `schema.md`
- [ ] Querying an unregistered vault → 422

## Acceptance criteria
- [ ] Endpoints per §10.1
- [ ] Query delegates to recovery (#26); grounding check (#25) is unbypassable
- [ ] No write path exists in this module

## Files
`src/groundtruth/api/query.py`, `src/groundtruth/api/notes.py`, tests

## Notes
Blocked by #26, #31. Invariants 1 and 3.
BODY

# ---------------------------------------------------------------- M8
mkissue "MCP server over streamable HTTP" "M8 — MCP + Web UI" "area:mcp" <<'BODY'
## Context
Lets external agents use groundtruth natively. Served from the same container as the API
and reusing the **same** auth layer — no second authentication path.

Spec: §10.2, §4.5.

## Write these tests first
- [ ] Server mounts at `/mcp` and completes an MCP initialize handshake
- [ ] Auth uses the layer from #30 — assert no separate token logic exists
- [ ] An unauthenticated request is rejected when `bearer` is configured
- [ ] Tool listing advertises exactly the 8 tools of §10.2
- [ ] Every tool takes a `vault` parameter, validated as in the API
- [ ] An unregistered vault produces a clean tool error, not a crash

## Acceptance criteria
- [ ] Streamable HTTP transport at a single endpoint, in-process with the API
- [ ] Auth shared, not reimplemented
- [ ] Adapter only

## Files
`src/groundtruth/mcp/server.py`, `tests/integration/test_mcp_server.py`

## Notes
Blocked by #30, #31. Needs the optional `mcp` extra.
BODY

mkissue "MCP tools: the eight adapters" "M8 — MCP + Web UI" "area:mcp,spec-critical" <<'BODY'
## Context
Thin adapters over the core engine — the same code paths as the REST API. Dedup,
atomicity, budgets, grounding and the schema lock all apply identically because the logic
lives below this layer, not in it.

Spec: §10.2.

## Write these tests first
- [ ] All eight tools present: `groundtruth_query`, `groundtruth_ingest`, `job_status`,
      `list_vaults`, `list_notes`, `read_note`, `get_schema`, `update_schema`
- [ ] `groundtruth_query` returns citations, or a refusal in the **same shape** as the API
- [ ] `groundtruth_ingest` honors dedup and `wait`
- [ ] **`update_schema` is refused when `schema_evolution` is disabled** (§5.2, §10.2)
- [ ] `update_schema` succeeds when enabled, preserving user prose (#20)
- [ ] `read_note` and `list_notes` are containment-checked
- [ ] No tool bypasses the write validator or the grounding check
- [ ] Tool bodies contain no business logic — assert by inspection in review

## Acceptance criteria
- [ ] Eight tools, each delegating to the same engine call the API uses
- [ ] The schema lock is enforced below this layer, not re-checked here

## Files
`src/groundtruth/mcp/tools.py`, `tests/integration/test_mcp_tools.py`

## Notes
Blocked by #33, #34, #35. `update_schema` is the only MCP tool that could violate the
schema lock — the spec calls it out explicitly for that reason.
BODY

mkissue "Web UI: ingest and query views" "M8 — MCP + Web UI" "area:web" <<'BODY'
## Context
htmx over FastAPI, no build step. The query view must present a refusal **as a refusal** —
a clear, legitimate outcome, not an error state and not an empty result.

Spec: §10.3.

## Write these tests first
- [ ] Ingest view: paste text, select a vault, submit → job id shown
- [ ] Job progress polls and reflects state changes
- [ ] A failed job shows the failing stage and reason
- [ ] Query view returns an answer with citations rendered as links into Browse
- [ ] **A refusal renders as a refusal** — distinct from an error, distinct from "no results"
- [ ] Both refusal reasons render with their own explanatory text
- [ ] Vault selector lists registered vaults

## Acceptance criteria
- [ ] Two views, server-rendered with htmx, no build step
- [ ] Refusals visually distinct from errors
- [ ] No business logic in templates or view handlers

## Files
`src/groundtruth/web/templates/`, `src/groundtruth/web/views.py`, tests

## Notes
Blocked by #33, #34. If a user reads a refusal as a bug, this view has failed.
BODY

mkissue "Web UI: read-only browse view" "M8 — MCP + Web UI" "area:web" <<'BODY'
## Context
Vault tree and note viewer. **Read-only** — editing happens in Obsidian (§3.3 non-goal).

Spec: §10.3.

## Write these tests first
- [ ] Tree renders the vault folder structure
- [ ] Selecting a note renders its content with frontmatter presented readably
- [ ] `[[wikilinks]]` are clickable and navigate within Browse
- [ ] A dangling wikilink renders as visibly broken rather than as a dead link
- [ ] **No edit, delete or create affordance exists anywhere** — assert the rendered HTML
- [ ] No write endpoint is reachable from this view
- [ ] Path traversal via a crafted URL is refused (#7)

## Acceptance criteria
- [ ] Tree + viewer, read-only
- [ ] Citations from the query view link here
- [ ] Markdown rendered safely — untrusted note content must not inject HTML/JS

## Files
`src/groundtruth/web/browse.py`, templates, tests

## Notes
Blocked by #34. Note content originates from ingested text: treat it as untrusted when
rendering.
BODY

# ---------------------------------------------------------------- M9
mkissue "Fixture vault and golden eval with must-refuse cases" "M9 — Eval + operations" "area:ops,spec-critical" <<'BODY'
## Context
Layer 2 of grounding verification, and the highest-value tests in the project. **The
must-refuse cases are the point** — they are the only mechanical way to catch the model
quietly filling a gap from its own knowledge, which is the exact failure this system
exists to prevent.

Spec: §9.2.

## Write these tests first
- [ ] A fixture vault (~20 notes) with known contents and a real `schema.md`
- [ ] **Answerable cases**: assert the fact *and* the citation
- [ ] **Must-refuse cases**: questions about information deliberately absent — assert a
      refusal. Include facts the model plausibly knows from pretraining (a famous company's
      founding year, say) that are **not** in the fixture vault. A correct-but-ungrounded
      answer is a FAILURE.
- [ ] A citation pointing at a nonexistent note fails the suite
- [ ] Budget-exhaustion refusals are exercised with a deliberately tiny budget
- [ ] The suite runs in CI (#2) and gates merges

## Acceptance criteria
- [ ] Fixture vault committed under `tests/fixtures/vault/`
- [ ] Both case classes, clearly separated and individually named
- [ ] Documented: how to add a case, and how to run against a real model locally
- [ ] Deterministic in CI — no live model call in the default run

## Files
`tests/fixtures/vault/`, `tests/integration/test_grounding_eval.py`

## Notes
Blocked by #2, #26. Pull this forward as soon as #26 lands — it is the measuring
instrument for #21's prompt iteration, which cannot be tuned without it.
BODY

mkissue "Observability: timings, token counts, LLM logging" "M9 — Eval + operations" "area:ops" <<'BODY'
## Context
Answers "what is an ingest costing me" and makes prompt regressions debuggable. LLM
logging is **off by default** — prompts contain ingested content.

Spec: §12.4, §7.11.

## Write these tests first
- [ ] Job records carry per-stage timings
- [ ] Job records carry per-role token counts from #14
- [ ] Structured logs emitted on every job stage transition
- [ ] LLM prompt/response logging is **off by default**
- [ ] When enabled, it writes to `<state-dir>/llm/<job-id>.jsonl`
- [ ] **No log or job record ever contains a secret** — assert against a config carrying a
      token
- [ ] Logging failure never fails a job

## Acceptance criteria
- [ ] Timings and token counts populated end to end
- [ ] LLM logging behind an explicit opt-in flag
- [ ] Secret redaction covered by a test, not by convention

## Files
`src/groundtruth/observability.py`, `tests/unit/test_observability.py`

## Notes
Blocked by #23, #26. Invariant 6.
BODY

mkissue "Docker image and compose" "M9 — Eval + operations" "area:ops" <<'BODY'
## Context
Docker-first deployment: a single image serving API, MCP and web, with named volumes for
vault repos and the state dir.

Spec: §4.2, §5.

## Write these tests first
Verification is largely operational — automate what can be automated.
- [ ] Image builds
- [ ] Container starts; health endpoint responds
- [ ] API, MCP and web are all served from the one container
- [ ] Named volumes mount vault repos and the state dir at the expected paths
- [ ] Secrets arrive via environment variables only (§11.4)
- [ ] **The state dir is not inside any vault repo** — assert the running layout
- [ ] Git identity is configured in the image so commits carry the `groundtruth` identity
- [ ] Data survives a container restart

## Acceptance criteria
- [ ] `Dockerfile` and `docker-compose.yml`
- [ ] Documented run instructions in the README
- [ ] Non-root user; no secrets baked into any layer

## Files
`Dockerfile`, `docker-compose.yml`, `README.md`

## Notes
Blocked by #33, #36, #37. Invariant 6 — a secret in an image layer is permanent.
BODY

echo
echo "==> done"
gh issue list -R "$REPO" --limit 60 --json number,title --jq 'length as $n | "\($n) issues in \(env.REPO // "repo")"' 2>/dev/null || true
