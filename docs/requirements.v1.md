# library — system requirements & description

A system to ingest unstructured text into Obsidian-compatible knowledge bases
("vaults") and to recover strictly grounded answers from them. It is composed of
two pipelines — **ingestion** and **recovery** — operating over one or more
independently registered vaults.

**MVP scope:** text-only input, single vault per query. Deferred: URL/file-format ingest, cross-vault queries.

---

## 1. Overview

- Two pipelines over a file-based, versioned knowledge base:
  - **Ingestion:** raw text in → LLM-tagged, reduced, organized notes out.
  - **Recovery:** query in → Markdown answer built **only** from information
    confirmed present in the database, or an explicit refusal.
- The database is a set of Markdown files, compatible with Obsidian (openable
  as a vault), organized with tags and `[[wikilinks]]`, versioned with git.
- Multiple vaults ("databases") can be registered. Each vault defines its own
  organization via a root `schema.md` file — the schema is per-vault, not global.
- Clients: REST API (primary), thin CLI, Web UI (htmx/simple JS), and an
  **MCP server** so external LLM agents can query and add data.

## 2. Architecture

- **Stack:** Python + FastAPI core engine. Delivery layers, in build order:
  1. **Core engine + REST API** (primary surface; all logic lives here),
  2. **Thin CLI** — a minimal client over the API (not a parallel
     implementation): roughly `library ingest`, `library query`,
     `library vaults list/add/remove`, `library jobs status`;
  3. **Web UI** — htmx/simple JS (see §6).
- **Deployment:** Docker-first. A single image (CLI + API + UI + MCP) with
  named volumes for the vault repos and the state dir.
- **Jobs / queues:**
  - Ingestion jobs run asynchronously; API returns a job id, with an optional
    `wait=true` to block until completion.
  - **Per-vault FIFO queue:** one ingest at a time within a vault (serializes
    git + file edits); concurrent ingests across different vaults are allowed.
  - Job state persisted as **JSON files** in a dedicated state dir (outside
    vaults), surviving restarts.
- **LLM:**
  - **OpenAI-compatible client** (works with OpenAI, Ollama, vLLM, etc. —
    any server exposing the compatible API). Local models preferred, remote
    allowed.
  - Models are **configurable per role** (`tag`, `reduce`, `answer`) with a
    shared default. Provider base URL + API key + model are set in
    configuration, not hardcoded.
- **Auth:**
  - A pluggable, isolated auth layer: core logic is agnostic to the strategy.
  - Default: bind to localhost, no auth. Optional static bearer token.
  - The same layer is reused by the API, Web UI, and MCP server — adding new
    strategies later must not require core changes.

## 3. Data layout (per vault)

```
<repo-root>/
├── <vault>/            # the Obsidian vault — what users browse
│   ├── schema.md       # fixed name, user-authored first version
│   │                   # documents folder organization + tag vocabulary
│   └── …notes…         # organized with tags + [[wikilinks]]
└── external/           # raw ingested texts — in the repo, OUT of the vault
    ├── <sha256>.txt    # immutable; never modified after write
    └── <sha256>.json   # manifest: hash, ingested_at, source label,
                        # job id, commit sha
```

Rules:

- **The git repo root is one level above the vault.** The vault is the content
  users browse; raw texts are committed alongside it in `external/` but the
  vault stays clean (Obsidian never sees them).
- **git is the versioning layer**: every commit captures changed notes, the
  raw text (when enabled), and a descriptive commit message. An optional remote
  git serves as sync mechanism.
- **`schema.md`** (name fixed by the system; content user-owned):
  - User creates the first version; it describes the folder organization and
    the tag vocabulary for that database.
  - The LLM may evolve it over time (e.g. recording new tags), **unless a
    vault-level config flag disables LLM modifications** — in which case the
    user-defined structure is locked.
- **Tags:**
  - The LLM may create new tags and must record them in `schema.md`.
  - Normalized form: lowercase, no spaces, words separated by `-`.

## 4. Ingestion pipeline

Text in → tagged, reduced, organized notes + (optionally) raw archive out.
Processing steps per job:

1. **Accept input** — text (MVP).
2. **Dedup** — SHA-256 of the input text. Exact-match hit for this vault →
   skip processing, return the existing notes + commit ref.
3. **Pre-sync** (if a remote is configured): `git pull --ff-only`.
   On conflict or non-ff state → **abort job, report, make no changes.**
4. **LLM processing** (single pass may combine steps 4a–4c):
   - **a. Tag** — extract/assign tags (normalized; new tags recorded in
     `schema.md` when allowed).
   - **b. Reduce** — distill the text to relevant information (summary/claims),
     not the full text.
   - **c. Organize** — the LLM decides **per item**: create a new note, update
     an existing note, or both — linking notes with `[[wikilinks]]` and
     integrating with previously stored information.
   - **Conflicts:** newer information wins; the previous version remains
     recoverable through git history.
5. **Raw archive** (configurable per vault, default on): write
   `external/<sha256>.txt` (immutable) + `external/<sha256>.json` manifest.
6. **Atomicity:** stage all changes (`git add`) and **commit only on full
   pipeline success**. Any failure at any stage → `git reset`, nothing
   persisted, job reported as failed.
7. **Commit:** one commit per ingest, authored with a **dedicated
   `library` git identity** (not the operator's identity).
   Commit message format: action (`ingest`), vault name, notes created/updated,
   tags, raw-text hash reference, short content excerpt.
8. **Push** (if `auto_push` enabled for the vault): `git push`. Push failure
   does not invalidate the local commit; reported in job result.
9. **Result** — async job record: state, notes touched, commit sha, or the
   dedup-skipped reference.

## 5. Recovery pipeline

Query in → grounded answer out.

- An **LLM agent** restricted to **read-only tools** over the vault:
  `ls`, `grep`, `read` (+ following `[[links]]` as navigation).
- Reads `schema.md` first, using it as the map of the database's organization.
- **Answer format:** Markdown, with `[[note]]` citations pointing at the
  evidence in the vault.
- **Hard rule — never guess:** the answer may only use information confirmed
  to exist in the database. If the question cannot be answered from the vault,
  the system **explicitly refuses** instead of supplementing with model
  knowledge.
- **No writes:** recovery never modifies the vault.
- **Scope:** one vault per query (MVP). Cross-vault queries are a deferred
  capability; the design (vault-selected engine calls) must not block adding
  it.

## 6. Web UI

- htmx / simple JS over the FastAPI backend; no separate build step.
- Three views:
  1. **Ingest** — paste/file text, select vault, submit, show job progress.
  2. **Chat/Query** — question, vault selector, answer with citations.
  3. **Browse** — vault tree + note viewer (read-only; editing happens in
     Obsidian).

## 7. MCP server (agent integration)

Lets external LLM agents connect to `library` natively — query information
and add data.

- **Transport:** Streamable HTTP (single endpoint, e.g. `/mcp`), served inside
  the same container as the API.
- **Auth:** reuses the **same token/auth abstraction layer** as the REST API
  (no separate auth path).
- **Vault scope:** all registered vaults are exposed; every tool takes a
  `vault` parameter, with the same validation/selection logic as the API.
- **Implementation:** a thin protocol adapter over the core engine — the same
  code paths as the REST API, no duplicated logic.

| Tool | Behavior |
|---|---|
| `library_query` | (vault, question) → MD answer with `[[citations]]`, or explicit refusal. Same grounded non-guessing rule as the API. |
| `library_ingest` | (vault, text, source?, wait?) → job id, or the result when `wait`. Same pipeline, dedup, and atomicity rules as the API. |
| `job_status` | (job_id) → state, steps, notes touched, commit sha / error. |
| `list_vaults` | registered vaults + metadata. |
| `list_notes` | (vault, path?, tag?) → note paths + tags. |
| `read_note` | (vault, path) → note content. |
| `get_schema` | (vault) → `schema.md` content. |
| `update_schema` | (vault, markdown, rationale) → edit `schema.md`. **Honors the schema-evolution flag: when LLM/modifications to `schema.md` are disabled for the vault, this tool is refused** — keeping the "user-defined structure is locked" guarantee consistent across all entry points. |

## 8. Configuration

- **Global config** (`config.yaml`):
  - Vault registry: name → path, git remote, per-vault flags.
  - Server settings (bind address, token, MCP endpoint).
- **Per-vault config** (`.library.yaml` adjacent to the vault):
  - `raw_archive`: on/off (default on) — commit raw texts to `external/`.
  - `auto_push`: bool (default off).
  - LLM per-role models (`tag`, `reduce`, `answer`) and shared default;
    provider base URL + model name reference an env var for the key.
  - `schema_evolution`: on/off — allow LLM/system modifications to
    `schema.md` (covers ingest-time tag recording and the MCP
    `update_schema` tool).
  - Token / auth binding (via the pluggable auth layer).
- **Secrets (API keys, tokens, remote credentials): environment variables
  only** — never written into config files, commit messages, or the vault.

## 9. Non-goals (MVP)

- Note editing in the Web UI (browse only; editing done in Obsidian).
- Multi-format ingestion (URL fetch, PDF/DOCX, images/vision).
- Cross-vault queries.
- Multi-user authentication beyond the optional single bearer token.
- Vector/embedding search — retrieval is LLM-agent-over-files (`ls/grep/read`).
- Full text preservation inside the vault (reduction only; raw text lives in
  `external/`).

## 10. Appendix — decisions log

| Topic | Decision |
|---|---|
| Interface | API-first (primary); thin CLI; Web UI (htmx/simple JS) |
| Ingest input (MVP) | Text only |
| LLM | OpenAI-compatible client; per-role models; local preferred, remote allowed |
| Retrieval | LLM agent with `ls/grep/read` over the vault |
| Reduction output | Summarize + update existing notes (LLM decides per item) |
| Recovery answer | MD with citations; always refuse rather than guess |
| Git layout | Repo root above vault; vault = repo content users browse |
| Raw texts | In repo, out of vault (`external/`), immutable + manifest |
| Commits | One per ingest; all-or-nothing; dedicated `library` git identity |
| Remote sync | `pull --ff-only` before, push after (optional); fail on conflict |
| Jobs | Per-vault FIFO; JSON-file state dir; async + `wait=true` |
| Dedup | SHA-256 exact match per vault → skip |
| Conflicts | Newer wins; git history preserves the old |
| Tags | LLM may create new tags; recorded in `schema.md`; normalized `lowercase-dashes` |
| `schema.md` | Fixed name; user-authored; LLM evolution optional and config-gated (also gates MCP `update_schema`) |
| Config | Global `config.yaml` + per-vault `.library.yaml`; secrets via env vars |
| Auth | Isolated pluggable layer; localhost default; optional bearer token |
| MCP | Streamable HTTP; reuses auth layer; all vaults param-selected; 8 tools (see §7) |
| Scale | Moderate (~10k notes) |
| Deployment | Docker-first, single image, named volumes |
| Build order | Core/API → thin CLI → Web UI (MCP alongside API) |
