# CLAUDE.md — working on groundtruth

Read this before touching anything.

`groundtruth` ingests unstructured text into an Obsidian-compatible vault and answers
questions **only** from what that vault actually contains — with citations, or an explicit
refusal. It never fills a gap from model knowledge.

**The spec is [`docs/requirements.md`](docs/requirements.md) and it is normative.** Issues
reference it by section (§7.6, §9.1, …). When this file and the spec disagree, the spec
wins and this file is a bug.

---

## Commands

Every command is stateless — no venv to activate, no shell state to lose between calls.

```bash
uv sync --all-extras          # set up / update the environment
uv run pytest                 # all tests
uv run pytest -k NAME         # one test or pattern
uv run pytest path/to/test.py::test_name -x   # one test, stop on failure
uv run ruff check .           # lint
uv run ruff format .          # format
uv run mypy src/              # type check
```

Before opening a PR, all four must be clean: `pytest`, `ruff check`, `ruff format --check`,
`mypy src/`.

---

## Architecture

```
src/groundtruth/
  config/      loading + 3-level precedence merge      §11
  models/      pydantic domain types
  storage/     notes, git, source index, job store     §5, §6, §12.1
  llm/         OpenAI-compatible client, per-role      §4.3
  retrieval/   SHARED agent loop: tools + budget       §7.4, §8   <- used by BOTH pipelines
  ingest/      pipeline, write tools, validator        §7
  recovery/    agent, grounding check                  §8, §9.1
  jobs/        per-vault FIFO queue, retry policy      §4.4, §12.2
  auth/        strategy protocol + registry            §4.5
  api/         FastAPI                                 §10.1   <- adapter only
  mcp/         8 MCP tools                             §10.2   <- adapter only
  web/         htmx templates                          §10.3   <- adapter only
```

**Layer rule.** `api/`, `mcp/`, and `web/` are adapters. They may not contain business
logic, touch git, or call the LLM. Everything beneath them is the core engine and must be
fully usable with no HTTP layer present — if a behavior can only be reached through
FastAPI, it is in the wrong place.

**`retrieval/` is shared on purpose** (ADR-3). "Find the notes relevant to this text" is the
same problem during ingestion and during recovery, so it is the same code. Do not fork it.

---

## TDD protocol

Non-negotiable, in this order:

1. **Write the failing test first.** Derive it from the issue's "Write these tests first".
2. **Run it and watch it fail.** A test you never saw fail proves nothing. If it passes
   immediately, the test is wrong.
3. **Write the minimum code to make it pass.** No speculative structure.
4. **Refactor** with the test green.

Rules that follow from this:

- Never write implementation before a red test exists.
- **Never edit a test to make it pass.** If a test seems wrong, stop and ask — see below.
- Never delete or `xfail` a failing test to get to green.
- Unit tests must not call a real LLM or network. Fake the client at its boundary.
- Tests touching git create a real temp repo (`tmp_path`); do not mock git.

---

## Invariants — never violate

These hold across every issue. No issue overrides them, and none of them can be inferred
from a single issue's text.

1. **Recovery never writes.** No note, no `schema.md`, no commit, no exception. The
   recovery agent gets read-only tools and nothing else. (§8.1)

2. **LLM output reaches disk only through the validator.** The model never emits a
   filesystem path — it calls `create_note(folder, title, body)` / `update_note(path, body)`
   and the validator gates everything before staging. Any code path that writes model
   output directly is a security bug. (§7.6, ADR-5)

3. **A grounding-check failure produces a refusal** — never a softened, hedged, or caveated
   answer. If citations are missing or a cited note does not exist, the answer is
   downgraded to a refusal. (§9.1)

4. **Budget exhaustion is a refusal too**, structurally identical to no-evidence. Never a
   partial answer behind a warning banner. (§8.2, ADR-6)

5. **Never ingest into a dirty working tree.** Rollback uses `git reset --hard` +
   `git clean -fd`, which would destroy a user's unsaved Obsidian edits. The precondition
   check and the rollback are a pair; neither is safe alone. (§7.1, ADR-4)

6. **Secrets are environment variables only.** Never in config files, commit messages,
   logs, job records, or the vault. Config references an env var *by name*. (§11.4)

7. **One ingest = one commit, all-or-nothing.** Any failure at any stage rolls back and
   commits nothing. This is what makes `git revert` a complete retraction. (§7.7, §12.3)

---

## Definition of done

- [ ] The issue's acceptance criteria are all ticked.
- [ ] Tests written first, and you watched them fail before they passed.
- [ ] `uv run pytest` green.
- [ ] `uv run ruff check .` and `uv run ruff format --check .` clean.
- [ ] `uv run mypy src/` clean.
- [ ] No new runtime dependency added without asking.
- [ ] `docs/requirements.md` unchanged (see below).

---

## Working the issues

- Issues carry `Blocked by #N`. Respect it — dependencies are real, not advisory.
- **One issue per PR.** Reference the issue and the spec section in the PR description.
- Branch naming: `<area>/<issue-number>-<slug>`, e.g. `storage/7-filename-sanitization`.
- Milestones M1→M9 are the intended build order. Within a milestone, order is flexible
  except where `Blocked by` says otherwise.

---

## When to stop and ask

Do not resolve these yourself:

- **The spec is ambiguous or silent** on something the issue needs.
- **An invariant appears to block the issue.** The issue is wrong, not the invariant.
- **A test looks wrong.** Never edit a test to make it pass.
- **You want to add a dependency.**
- **You want to change `docs/requirements.md`.** Spec changes are their own PR, with
  rationale, reviewed on their own merits — never folded into an implementation PR.

---

## Context worth having

- **`schema.md`** is the user-authored map of a vault: its folder organization and tag
  vocabulary. Both pipelines read it first. The system may only append to it when
  `schema_evolution` is enabled for that vault — and that gate applies at *every* entry
  point, including the MCP `update_schema` tool. (§5.2)

- **Notes are one-per-topic**, not one-per-claim (ADR-9), with required YAML frontmatter
  carrying `tags` and an append-only `sources` list of SHA-256 hashes. Frontmatter is the
  index — retrieval is `grep` over these files, with no separate index to keep in sync.

- **The source index lives in the state dir, not in the repo** (ADR-7). Dedup and
  provenance must keep working when `raw_archive` is disabled.

- **The hardest part of this project is not in any issue.** Whether the reduce step (§7.5)
  produces a vault worth querying is a prompt-iteration problem. Issue #21 will take
  several passes; that is expected, not a failure.
