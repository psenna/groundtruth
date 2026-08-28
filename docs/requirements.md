# groundtruth — system requirements

**Status:** design, pre-implementation · **Revision:** 2 · **Date:** 2026-08-28
**Supersedes:** `requirements.v1.md` (project name: `library`)

---

## 1. Purpose

`groundtruth` turns unstructured text into an Obsidian-compatible knowledge base,
and answers questions **only** from what that knowledge base actually contains.

The name states the guarantee. *Ground truth* is the verified reality you check a
model's output against — not the model's output itself. An answer from
`groundtruth` cites the notes it came from, or it refuses. It never fills a gap
from the model's own knowledge.

Two pipelines over one file-based, git-versioned store:

- **Ingestion** — raw text in → tagged, distilled, cross-linked notes out.
- **Recovery** — question in → Markdown answer with `[[citations]]`, or an
  explicit refusal.

Everything else in this document exists to make that guarantee hold.

---

## 2. Glossary

Revision 1 used *vault*, *database*, *knowledge base*, and *repo* interchangeably.
These terms are now fixed and used consistently throughout.

| Term | Meaning |
|---|---|
| **repo** | The git repository. Its root is one level **above** the vault. |
| **vault** | The directory Obsidian opens. Contains `schema.md` and all notes. Never contains raw source text. |
| **note** | One Markdown file in the vault, about one topic or entity (§6). |
| **source** | One ingested text, identified by the SHA-256 of its content. |
| **job** | One asynchronous ingestion run. Produces at most one git commit. |
| **schema.md** | User-owned file at the vault root describing folder organization and tag vocabulary. The map the LLM reads before doing anything. |
| **state dir** | Server-owned directory outside every repo. Holds job records and the per-vault source index. Never committed. |

---

## 3. Scope

### 3.1 In scope for MVP

- Text input only.
- Single vault per query.
- Delivery surfaces: **core engine + REST API**, **MCP server**, **Web UI**.

### 3.2 Deferred (design must not preclude)

| Deferred | Constraint it places on the MVP design |
|---|---|
| Thin CLI | API must be complete enough that a CLI is a pure client — no logic below it. |
| URL / PDF / DOCX / image ingest | Ingestion must accept text at a boundary an adapter can feed. |
| Cross-vault queries | Every engine call takes an explicit vault; nothing may assume exactly one. |

### 3.3 Non-goals

- **Note editing in the Web UI.** Editing happens in Obsidian. The UI browses.
- **Vector or embedding search.** Retrieval is an LLM agent over files (§8).
  Rationale in ADR-2.
- **Full-text preservation inside the vault.** Notes are distillations. The
  original text lives in `external/` when archiving is enabled (§5).
- **Near-duplicate detection.** Dedup is exact-hash only (§7.2). Re-pasting a
  text with a trailing newline change will re-ingest it.
- **Multi-user authentication** beyond a single shared bearer token.
- **Automatic conflict reconciliation.** When sources disagree, newer wins and
  git history holds the old value (ADR-8).

---

## 4. Architecture

### 4.1 Stack

Python + FastAPI. All logic lives in the **core engine**; every delivery surface
is a thin adapter over it with no duplicated behavior.

```
                    ┌──────────────────────┐
   REST API ───────►│                      │
   MCP server ─────►│     core engine      │──► vault repos (git)
   Web UI ─────────►│                      │──► state dir
   (CLI, deferred)  └──────────────────────┘
```

### 4.2 Deployment

Docker-first. A single image serving API + MCP + Web UI. Named volumes for the
vault repos and for the state dir.

### 4.3 LLM access

- **OpenAI-compatible client.** Works against OpenAI, Ollama, vLLM, or anything
  exposing the compatible API. Local models preferred, remote allowed.
- **Models configurable per role** — `tag`, `reduce`, `answer` — with a shared
  default. Base URL, model name, and API-key env-var reference all come from
  configuration; nothing is hardcoded.

### 4.4 Jobs

- Ingestion is asynchronous. The API returns a job id; `wait=true` blocks until
  the job reaches a terminal state.
- **Per-vault FIFO queue.** One ingest at a time within a vault — this serializes
  all git and file mutation. Ingests across different vaults run concurrently.
- Job records persist as JSON files in the state dir and survive restarts.

### 4.5 Authentication

A pluggable strategy layer: an `AuthStrategy` protocol, a registry mapping
config names to implementations, and modules per strategy. Ships with `none`
(default, bound to localhost) and `bearer` (single static token).

The API, MCP server, and Web UI all authenticate through this one layer. The
core engine never sees a request, a header, or a token — it receives a resolved
principal or nothing. Adding a strategy must require no changes to existing code.

> Accepted cost: this is more structure than two strategies need today. The
> decision and its tradeoff are recorded in ADR-11.

---

## 5. Data layout

```
<repo-root>/                      # the git repo
├── <vault-name>/                 # THE VAULT — what Obsidian opens
│   ├── schema.md                 # fixed filename, user-authored
│   └── …notes…                   # organized in folders, tagged, wikilinked
├── external/                     # raw sources — in the repo, OUT of the vault
│   ├── <sha256>.txt              # immutable; never modified after write
│   └── <sha256>.json             # manifest: hash, ingested_at, source label,
│                                 #   job id, commit sha, notes touched
├── .groundtruth.yaml             # per-vault configuration (§11)
└── .gitignore
```

Outside every repo:

```
<state-dir>/
├── jobs/<job-id>.json            # job records (§12.1)
├── index/<vault>.json            # source index: sha → job id, commit sha,
│                                 #   notes touched, ingested_at
└── llm/<job-id>.jsonl            # prompt/response log — OFF by default (§12.4)
```

### 5.1 Rules

- **The repo root sits one level above the vault.** Raw sources are versioned
  alongside the vault but Obsidian never sees them.
- **git is the versioning layer.** Every ingest is exactly one commit. An
  optional remote provides sync and backup.
- **The source index lives in the state dir, not in `external/`.** This is a
  change from revision 1 and is load-bearing: dedup (§7.2) and note→source
  provenance (§6) must keep working when `raw_archive` is disabled. See ADR-7.
  - With `raw_archive: on`, a claim traces to a source **and** the source text
    is re-readable.
  - With `raw_archive: off`, a claim still traces to a source hash, job, and
    commit — but the original text is not recoverable.
- **The state dir is never committed** and holds no secrets.

### 5.2 `schema.md`

Filename is fixed by the system; content is owned by the user. It documents the
vault's folder organization and its tag vocabulary, and it is the first thing
both pipelines read.

**The ingestion pipeline never writes to `schema.md`.** It is read-only to the
system on every automated path. The user's structure is protected structurally
rather than by a configuration flag — there is no write path to disable.

The single exception is the MCP `update_schema` tool (§10.2), which exists so an
external agent can propose vocabulary changes. It is gated by
**`allow_schema_writes`, which defaults to `false`** — writing into a human's
document is opt-in. This invariant is stated once, here, and referenced elsewhere.

`schema.md` is **prescriptive**: it tells the system how the user wants this vault
organized ("use `vendor`, not `supplier`"). It is not an inventory of what the
vault currently contains — see §5.3.

### 5.3 Tags

Normalized form: lowercase, no spaces, words separated by `-`.

The LLM may introduce new tags. It does **not** record them anywhere: the
**active vocabulary is derived** from note frontmatter (§6) rather than stored,
and injected into prompts alongside `schema.md`.

```
prompt context = schema.md (verbatim, prescriptive — what the user wants)
               + derived vocabulary (computed, descriptive — what exists)
```

Deriving rather than storing removes a whole class of failure. A written tag list
is a second copy of information that already lives in every note's frontmatter,
and two copies drift; a derived list is correct by construction. It also means no
automated process needs write access to a user-authored file.

**Caching.** The derived vocabulary is keyed on the vault's git `HEAD` sha. It is
therefore invalidated exactly when the vault changes and never otherwise — no
staleness heuristics, no TTL. Tags are ranked by frequency, so the list can be
truncated for context without losing the ones that matter.

---

## 6. Note format

Retrieval is grep-based, so note structure *is* the index format. It is
therefore normative, not advisory.

```markdown
---
title: Acme Corp
tags: [company, vendor]
sources:
  - a1b2c3d4e5f6…          # SHA-256 of an ingested source
  - 9f8e7d6c5b4a…
created: 2026-03-01
updated: 2026-08-27
---

Acme ships [[Widget Platform]] and was founded in 1996.

Their contract renews annually — see [[Vendor Contracts]].
```

- **Frontmatter is required** on every system-written note. Obsidian reads
  `tags` natively; `sources` gives every note machine-readable provenance.
- **`sources` is append-only.** Each ingest that touches a note adds its hash.
  This is what makes "where did this claim come from" answerable.
- **Body is prose with `[[wikilinks]]`.** Links are the only cross-note
  structure; there is no separate index to maintain.

### 6.1 Granularity

**One note per topic or entity** — a company, a person, a project, a concept.
Ingestion appends to or revises the relevant topic note rather than creating a
new note per fact.

This keeps the vault browsable by hand in Obsidian and keeps note count within
the scale target (§14). The accepted cost is that a citation points at a note
rather than at an individual claim. Alternatives considered in ADR-9.

---

## 7. Ingestion pipeline

Text in → distilled, organized, linked notes out, plus an optional raw archive.

### 7.1 Precondition: clean working tree

Before anything else, the job checks `git status --porcelain`. **If the working
tree is dirty, the job fails immediately** and changes nothing.

This is not a convenience check. Rollback requires `git reset --hard` and
`git clean -fd`, which would destroy unsaved Obsidian edits. Refusing up front is
the only way the atomicity guarantee in §7.7 can be honest. See ADR-4.

### 7.2 Dedup

SHA-256 of the input text, looked up in the source index for this vault
(`<state-dir>/index/<vault>.json`). On an exact match the job short-circuits and
returns the existing notes and commit ref without any LLM call.

Exact-match only. Whitespace or encoding differences produce a different hash and
a fresh ingest.

### 7.3 Pre-sync

If a remote is configured: `git pull --ff-only`. On conflict or a non-fast-forward
state the job **aborts and reports, making no changes.**

### 7.4 Retrieval — locate existing notes

The ingesting LLM runs the **same read-only agent loop the recovery pipeline
uses** (§8): read `schema.md`, then `ls` / `grep` / `read` / follow `[[links]]` to
find the notes this text relates to.

This is deliberate. "Which notes are relevant to this text?" is the same problem
recovery solves, so it is the same code — one retrieval engine to build, test, and
improve, benefiting both pipelines. See ADR-3. The agent is subject to the same
budget caps as recovery (§8.2).

### 7.5 LLM processing

May be one pass or several; the following are requirements on the output, not a
prescribed call structure.

- **Tag** — assign normalized tags, guided by `schema.md` and the derived active
  vocabulary (§5.3). New tags need no recording step; they become part of the
  derived vocabulary as soon as the note carrying them is committed.
- **Reduce** — distill the text to the information worth keeping: claims, facts,
  and relationships relevant to the vault's subject matter as described by
  `schema.md`. Not a transcript, not the full text. Discard narration, hedging,
  and restatement.
- **Organize** — decide per item whether to create a note, update a note, or
  both; link related notes with `[[wikilinks]]`; integrate with what is already
  stored.
- **Conflicts** — when new information contradicts a stored claim, the newer
  value replaces it. The previous value stays recoverable through git history.
  Contradictions are not surfaced to the user (ADR-8).

### 7.6 Write validation

**The LLM never emits a filesystem path.** It writes exclusively through two
constrained tools:

```
create_note(folder, title, body)     # folder must be listed in schema.md
update_note(path, body)              # path must already exist
```

A validator gates everything before staging. **Any violation fails the whole job
with nothing staged:**

| Check | Rule |
|---|---|
| Folder | Must appear in `schema.md`. No creation of undeclared folders. |
| Title → filename | Sanitized; no path separators, no traversal, no leading dots. |
| Containment | Every resolved path must sit inside the vault directory. |
| Note count | At most `max_notes_per_ingest` notes touched (default 10). |
| Note size | At most `max_note_bytes` per note (default 64 KB). |
| Link integrity | Every `[[link]]` must resolve to an existing note, or to one created in this same job. |
| Frontmatter | Present, parseable, and carrying the required keys of §6. |

Constraining by construction — rather than sanitizing bad output — is what keeps
an LLM from being an unbounded write primitive into a git repo. See ADR-5.

### 7.7 Atomicity

All changes are staged with `git add`. **The commit happens only on full pipeline
success.** Any failure at any stage triggers `git reset --hard` + `git clean -fd`
and the job is reported failed with the stage that broke.

This rollback is safe **because** §7.1 guaranteed the tree was clean at job start.
The two rules are a pair; neither works alone.

### 7.8 Raw archive

Configurable per vault, default on. Writes `external/<sha256>.txt` (immutable,
never modified after write) and `external/<sha256>.json` (manifest: hash,
`ingested_at`, source label, job id, commit sha, notes touched).

Independent of the source index (§5.1) — dedup and provenance do not depend on
this flag.

### 7.9 Commit

One commit per ingest, authored with a **dedicated `groundtruth` git identity**,
never the operator's. Message format:

```
ingest(<vault>): <short subject>

notes:   created A, B · updated C
tags:    company, vendor
source:  sha256:a1b2c3d4…
job:     01J8X…

<content excerpt, ~200 chars>
```

### 7.10 Push

If `auto_push` is enabled: `git push`. **A push failure does not invalidate the
local commit** — the ingest succeeded; the sync did not. Reported in the job
result.

### 7.11 Result

The job record (§12.1) carries: terminal state, notes created and updated, commit
sha, token usage, per-stage timings, and on failure the stage and error — or, for
a dedup short-circuit, the reference to the prior ingest.

---

## 8. Recovery pipeline

Question in → grounded answer out.

### 8.1 Method

An LLM agent with **read-only tools** over one vault: `ls`, `grep`, `read`, and
following `[[links]]` as navigation. It reads `schema.md` first and uses it as
the map of the vault's organization.

**Recovery never writes.** No note, no `schema.md`, no commit, no exception.

### 8.2 Budget

| Limit | Default | Overridable per vault |
|---|---|---|
| `max_tool_calls` | 30 | yes |
| `max_wall_clock` | 60 s | yes |
| `grep_max_matches` | 50 | yes |
| `grep_max_bytes` | 64 KB | yes |
| `read_max_bytes` | 32 KB per note | yes |

**On budget exhaustion the agent refuses.** It does not return a partial answer
with a caveat.

> "Could not establish ground truth for this question within the search budget."

Exhaustion and no-evidence produce the same honest outcome: no answer. A partial
answer behind a warning banner trains users to ignore warning banners. See ADR-6.

### 8.3 Answer format

Markdown, with `[[note]]` citations pointing at the evidence. Every substantive
claim carries a citation.

### 8.4 The grounding rule

**Never guess.** An answer may use only information confirmed present in the
vault. When the question cannot be answered from the vault, the system refuses
explicitly rather than supplementing from model knowledge.

Refusals are a first-class result, not an error. They return HTTP 200 with a
structured `refused` outcome and a reason (`no_evidence` or `budget_exhausted`).

### 8.5 Scope

One vault per query. Cross-vault is deferred (§3.2); every engine call already
takes an explicit vault so adding it is additive.

---

## 9. Grounding verification

The product's entire value is that answers are grounded. That claim is verified
in two layers rather than asserted.

### 9.1 Runtime check — every answer

Before an answer is returned:

1. It contains at least one `[[citation]]`.
2. Every cited note exists on disk in that vault.

**Failing either check downgrades the answer to a refusal.** This catches
fabricated note names — the cheapest and most common grounding failure.

It does not catch a fabricated claim under a real citation. That is §9.2's job.

### 9.2 Fixture eval — CI

A small fixture vault (~20 notes) with known contents, and two classes of test:

- **Answerable** — questions whose answer is present. Assert the fact and the
  citation.
- **Must-refuse** — questions about information deliberately *absent* from the
  fixture vault. Assert a refusal.

**The must-refuse cases are the point.** They are the only mechanical way to
catch the model quietly filling a gap from its own knowledge, which is the exact
failure this system exists to prevent. A prompt change or model swap that breaks
grounding fails CI here.

---

## 10. Interfaces

### 10.1 REST API

Primary surface. All logic lives beneath it; every other surface is a client.

| Endpoint | Purpose |
|---|---|
| `POST /vaults` | Register a vault. Adopts an existing repo by default; `init: true` scaffolds a new one (§13.1). |
| `GET /vaults` | Registered vaults and metadata. |
| `DELETE /vaults/{name}` | Deregister. Never deletes files on disk. |
| `POST /ingest` | `(vault, text, source?, wait?)` → job id, or the result when `wait`. |
| `GET /jobs/{id}` | Job record (§12.1). |
| `POST /query` | `(vault, question)` → grounded answer or structured refusal. |
| `GET /notes` | `(vault, path?, tag?)` → note paths and tags. |
| `GET /notes/{path}` | Note content. |
| `GET /schema` | `(vault)` → `schema.md` content. |

### 10.2 MCP server

Lets external LLM agents use `groundtruth` natively.

- **Transport:** Streamable HTTP at a single endpoint (`/mcp`), served from the
  same container as the API.
- **Auth:** the same layer as the API (§4.5). No separate path.
- **Scope:** all registered vaults exposed; every tool takes a `vault` parameter
  with the same validation as the API.
- **Implementation:** a thin protocol adapter over the core engine. Same code
  paths as the REST API — dedup, atomicity, budgets, grounding, and the
  `allow_schema_writes` gate all apply identically and are not restated per tool.

| Tool | Signature |
|---|---|
| `groundtruth_query` | `(vault, question)` → answer with citations, or refusal |
| `groundtruth_ingest` | `(vault, text, source?, wait?)` → job id or result |
| `job_status` | `(job_id)` → job record |
| `list_vaults` | `()` → vaults and metadata |
| `list_notes` | `(vault, path?, tag?)` → note paths and tags |
| `read_note` | `(vault, path)` → note content |
| `get_schema` | `(vault)` → `schema.md` content |
| `update_schema` | `(vault, markdown, rationale)` → edit `schema.md`. **Refused unless `allow_schema_writes` is enabled; it defaults to `false`** (§5.2). The only write path to `schema.md` in the system. |

### 10.3 Web UI

htmx / simple JS over FastAPI. No build step.

1. **Ingest** — paste text, select vault, submit, watch job progress.
2. **Query** — question, vault selector, answer with citations, refusals shown
   as refusals rather than as errors.
3. **Browse** — vault tree and note viewer, read-only. Editing is Obsidian's job.

---

## 11. Configuration

### 11.1 Precedence

Resolved in this order, most specific first. This ordering is documented behavior
and must be covered by tests.

```
1. <repo-root>/.groundtruth.yaml     per-vault, committed with the vault
2. config.yaml                       global defaults
3. built-in defaults
```

The global config path comes from `--config` or `$GT_CONFIG`, defaulting to
`/etc/groundtruth/config.yaml`. It is **not** in the state dir — it declares
where the state dir is.

Merging is per-key, not per-section: a vault overriding one model does not
inherit-or-clobber the rest of the model block.

### 11.2 Global — `config.yaml`

```yaml
server:
  bind: 127.0.0.1
  auth: none              # none | bearer
  mcp_endpoint: /mcp
state_dir: /var/lib/groundtruth
job_retention_days: 7

vaults:                   # registry: name → repo root
  work: /data/work-repo
  personal: /data/personal-repo

defaults:                 # applied to every vault unless overridden
  raw_archive: true
  auto_push: false
  allow_schema_writes: false   # gates MCP update_schema only (§5.2)
  models:
    default:
      base_url: http://localhost:11434/v1
      model: qwen2.5:14b
      api_key_env: GT_API_KEY
    tag:    {model: qwen2.5:7b}      # inherits base_url + api_key_env
    reduce: {model: qwen2.5:14b}
    answer: {model: qwen2.5:32b}
  limits:
    max_notes_per_ingest: 10
    max_note_bytes: 65536
    max_tool_calls: 30
    max_wall_clock_s: 60
```

### 11.3 Per-vault — `.groundtruth.yaml`

Lives at the repo root, committed with the vault, so settings travel with a
clone. Same keys as `defaults` above; any subset.

### 11.4 Secrets

**Environment variables only.** Never written into config files, commit messages,
job records, logs, or the vault. Configuration references an env var by name; it
never contains a value.

---

## 12. Operations

### 12.1 Job records

One JSON file per job in `<state-dir>/jobs/`, containing: id, vault, state,
per-stage timings, token usage per role, notes created and updated, commit sha,
source hash, and on failure the stage and error.

Retained `job_retention_days` (default 7), swept on startup and daily.

### 12.2 Failure and retry

Errors are classified. **Transient errors retry up to twice with backoff;
everything else fails immediately.** A failed job never blocks the queue — it is
recorded and the vault's queue proceeds to the next job.

| Transient — retry ×2 | Terminal — fail now |
|---|---|
| Connection refused (local model server restarting) | Write-validator rejection (§7.6) |
| HTTP 429 / 502 / 503 | Git conflict or non-fast-forward |
| Read timeout | Dirty working tree (§7.1) |
| | Malformed or unparseable LLM output |

Retrying is worth the complexity specifically because a local-first design leans
on a model server that restarts and OOMs. Retrying a validator rejection would
not be — it fails identically the second time.

### 12.3 Retraction

There is no retraction API. **`git revert <commit>` is the supported procedure**
and the one-commit-per-ingest rule already makes it complete: reverting an ingest
restores the prior note contents and removes its `external/` archive together.

The source index entry is removed on revert so a corrected re-ingest is not
skipped by dedup.

Reverting an old ingest may conflict with later edits to the same notes. That is
ordinary git; resolve by hand.

### 12.4 Observability

- **Job records** carry per-stage timings and per-role token counts — sufficient
  to answer "what is an ingest costing me."
- **Structured logs** for every job stage transition.
- **LLM call logging** — full prompt and response to the state dir, off by
  default, for debugging prompt regressions. Never enabled by default: prompts
  contain ingested content.

---

## 13. Vault lifecycle

### 13.1 Registration

`POST /vaults` **adopts** an existing repo. It validates and refuses with the
specific problem when something is missing:

- path is a git repository
- `<repo>/<vault-name>/` exists
- `<repo>/<vault-name>/schema.md` exists
- working tree is clean

`POST /vaults {init: true}` **scaffolds** a new one instead: `git init`, the
layout of §5, a starter `schema.md`, a default `.groundtruth.yaml`, a
`.gitignore`, and an initial commit.

The starter `schema.md` is documentation as much as scaffolding — `schema.md` is
the most under-explained concept in this system, and the template is where a user
learns what belongs in it:

```markdown
# Schema

## Folders
<!-- Describe how notes are organized. The LLM may only create notes in
     folders listed here. -->
- companies/ — organizations
- people/ — individuals
- projects/ — ongoing work

## Tags
<!-- How you want things tagged. This is guidance for the system, not an
     inventory — the tags actually in use are derived from your notes (§5.3),
     so you never have to maintain a list here.
     Lowercase, dash-separated. -->
- Use `vendor` for suppliers, not `supplier`.
- Prefer `project` over `initiative`.
- Tag people with `person` plus their organization.
```

### 13.2 Deregistration

`DELETE /vaults/{name}` removes the registry entry and the vault's source index.
**It never deletes files on disk.** The repo is the user's.

---

## 14. Non-functional requirements

These are targets, not measurements. They exist to be falsified once the system
runs.

| Property | Target |
|---|---|
| Vault size | ~10,000 notes |
| Ingest latency (p50) | < 60 s |
| Query latency (p50) | < 30 s |
| Concurrent ingests | 1 per vault; N vaults in parallel |
| Restart behavior | Queued and running jobs recoverable from the state dir |

---

## 15. Open questions

1. **Name collision.** `groundtruth` needs a PyPI, GitHub, and Docker Hub check
   before anything is published. The `gt` CLI alias collides with several
   existing tools; treat it as a local convenience, not a claim.
2. **Latency and scale targets (§14) are guesses.** Replace with measurements
   after the first real vault exists.
3. **Reduce quality is the real product risk.** Everything else here is
   mechanically verifiable; whether the distillation produces a vault worth
   querying is a prompt-iteration problem with no design answer. Budget for it.
4. ~~**`schema.md` editing mechanics.**~~ **Resolved** — the system no longer
   writes to `schema.md` at all. See §5.3 and ADR-12.
5. **`schema.md` context growth.** Largely resolved by ADR-12: `schema.md` can no
   longer grow from system writes, so it stays whatever size the user wrote, and
   the derived vocabulary is frequency-ranked and therefore safely truncatable.
   What remains is a display budget — how many tags to show before truncating —
   which needs a real vault to tune.
6. **Cross-vault query semantics.** When it arrives: fan out and merge, or route
   to one vault? Affects nothing today, but the answer shapes the citation format.

---

## 16. Decision log

Only decisions whose reasoning is not evident from the spec body. Everything else
is stated once above and not repeated here.

**ADR-1 — Files and git as the database.**
*Rejected:* SQLite, a document store, a vector database.
Obsidian compatibility requires plain Markdown on disk, and git supplies
versioning, sync, and undo for free. Retraction (§12.3) needs no code because of
this choice.

**ADR-2 — Agent-over-files retrieval; no embeddings.**
*Rejected:* vector search over chunked notes.
Grounding must be *verifiable*: a citation names a file that either exists or
does not (§9.1). Embeddings add an index that can drift from the files, and
similarity scores cannot be checked for truth. At ~10k notes, `grep` is adequate.

**ADR-3 — One retrieval engine shared by both pipelines.**
*Rejected:* a deterministic tag/keyword shortlist for ingest.
"Find the notes relevant to this text" is the same problem in both directions.
Sharing it means one implementation to test and tune, and every retrieval
improvement helps ingestion and recovery at once. Accepted cost: every ingest
runs an agent loop, so ingests are slower and more expensive than a heuristic.

**ADR-4 — Refuse to ingest into a dirty working tree.**
*Rejected:* auto-stash; a temporary git worktree per job.
Rollback needs `reset --hard` + `clean -fd`, which destroys unsaved Obsidian
edits. Stashing turns a failed ingest into a stash-pop conflict in the user's
working tree — a worse failure than the one it prevents. A worktree per job
works but adds machinery for a case a precondition eliminates. Revision 1's
atomicity claim was not achievable without this rule.

**ADR-5 — Constrained write tools instead of free-form paths.**
*Rejected:* sanitize-and-continue.
An LLM emitting arbitrary paths into a git repo is an unbounded write primitive.
Sanitizing keeps jobs from failing but erodes vault quality silently, and nobody
reviews warnings. Failing loudly on a validator rejection is the correct
behavior for a system whose value is trustworthiness.

**ADR-6 — Budget exhaustion produces a refusal, not a partial answer.**
*Rejected:* partial answers behind a "search incomplete" banner.
Users learn to ignore banners. Since "I could not verify this" and "there is no
evidence" have the same practical consequence — do not rely on this — they get
the same output. This keeps §8.4 absolute rather than conditional.

**ADR-7 — Source index in the state dir, not in `external/`.**
*Rejected:* revision 1's layout, where the hash lived only in
`external/<sha>.json`.
That made dedup and note provenance silently depend on the `raw_archive` flag:
turning archiving off broke both. Separating them lets `raw_archive` control
exactly one thing — whether the original text is re-readable.

**ADR-8 — Newer wins on contradiction.**
*Rejected:* recording both claims with source attribution; flagging conflicts
with a `#conflict` tag.
Attribution keeps more information but makes notes progressively less readable,
and readability in Obsidian is a primary requirement. Git history preserves the
prior value. Accepted cost: a source that is wrong-but-recent silently overwrites
one that was right, and nothing surfaces the disagreement. Revisit if it bites.

**ADR-9 — One note per topic or entity.**
*Rejected:* one note per atomic claim (Zettelkasten); a source-summary note plus
thin topic indexes.
Atomic claims give precise citations but blow past the 10k-note target quickly
and produce a vault no human wants to browse. The source-plus-index model has
excellent provenance but conflicts with ADR-8 — with no single place a fact
lives, "newer wins" has nothing to overwrite.

**ADR-10 — CLI deferred to post-MVP.**
*Rejected:* revision 1's build order (core → CLI → Web UI).
The API, MCP, and Web UI already cover interactive, agent, and programmatic use.
A CLI is a pure client over a complete API and can be added at any point without
design consequences — which is precisely why it should not consume MVP effort.

**ADR-11 — Pluggable auth registry, accepted knowingly.**
*Rejected:* a single `authenticate()` dependency with two branches.
Two strategies do not require a registry, and this is more structure than YAGNI
would allow. Chosen deliberately so that API, MCP, and Web UI cannot diverge in
how they authenticate, and so a third strategy is additive. Recorded here so the
cost is visible rather than accidental.

**ADR-12 — `schema.md` is never written by the ingestion pipeline; tag vocabulary
is derived, not stored.**
*Rejected:* an anchored managed section inside `schema.md`; a system-owned fenced
block; a full LLM rewrite of the file.
Revision 2 originally had ingestion append newly created tags to `schema.md`,
gated by a `schema_evolution` flag. Every mechanism for doing so safely required
the system to own a region of a user-authored document, and left two copies of the
tag vocabulary — one written, one implied by note frontmatter — that could drift.

Separating the file's two roles dissolved the problem. `schema.md` is
*prescriptive* (how the user wants this vault organized) and can only be
user-authored. The tag inventory is *descriptive* (what tags exist) and is already
recorded in every note's frontmatter, so it can be derived on demand and injected
into prompts. Deriving is correct by construction, needs no markers in the user's
file, and removes the write path rather than guarding it.

Consequences: the flag survives only to gate the MCP `update_schema` tool, renamed
to `allow_schema_writes` and defaulted to `false` — its original default of "on"
existed because ingestion needed it, and ingestion no longer writes. The derived
vocabulary is cached against the vault's git `HEAD` sha, which invalidates exactly
when the vault changes.
