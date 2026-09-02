# Scoring rubric

Applied **per phase**, after `scripts/check.mjs` reports no hard failures, by
reading the full note dump and the `queries` rows against
`gold/after-phase-<N>.md`. Score out of 100. The deterministic checks feed a few
categories but the judgement is on synthesis and organisation, which no script
can see.

A phase that hard-fails the check script scores 0 and the run stops there.

| # | Category | Weight | What full marks looks like |
|---|---|---|---|
| 1 | **Grounding / no hallucination** | 18 | Every claim in every note is traceable to a sentence in an ingested corpus doc. No invented specifics (no tool names, vendors, numbers) that the corpus does not contain. Nothing carried over from model knowledge about "how software teams usually work". |
| 2 | **Query faithfulness** | 12 | Every grounded query returns an answer that is correct *per the vault* and cites a real, relevant note. Every out-of-scope query is refused. No answer hedges or half-answers an out-of-scope question. |
| 3 | **Granularity** | 12 | One note per topic/entity — not one per claim, not six thin notes for one subject. A large corpus doc that covers several real topics becomes a small number of substantial notes, not a spray. No note under ~400 characters of real body. |
| 4 | **Consolidation across ingests** | 12 | The re-ingested duplicate is deduped (check reports it). `test-strategy-and-the-pyramid` (P1) and `testing-strategy` (P3) end up as **one** testing note or a tight overview+detail pair, not two overlapping notes. Same for `branching-and-pull-requests` (P2) vs `delivery-and-release-management` (P3). Later docs *update* earlier notes rather than duplicating them. |
| 5 | **Contradiction handling (§7.5)** | 8 | The feature-branch lifetime resolves to **one working day** (the phase-2/3 value), not two (phase-1), not both, not a note *about* the disagreement. The superseding is silent — git history holds the old value. |
| 6 | **Schema compliance** | 8 | Every note in a declared folder, spelled exactly. No invented sub-folders (the ADR doc mentions `architecture/decisions/` — the model must resist creating it). Content lands in the *right* folder (an infra topic in `infrastructure/`, not `architecture/`). |
| 7 | **Wikilink quality** | 7 | No dangling links (deterministic). Beyond that: links are *meaningful* — a note links to the notes a reader would actually want next, not to every note that shares a word. Overview notes link to their detail notes. |
| 8 | **Tag quality** | 7 | 2–6 normalized tags per note (deterministic), and they name what *that note* is about — not the source doc, not the whole area. `anti-pattern` used where the schema asks. Tag vocabulary is consistent across notes (not `ci-cd` in one and `pipeline` in another). |
| 9 | **Synthesis quality** | 10 | Notes read as written prose with structure, not as a reformatted bullet dump of the reduce step. An overview note *summarises and points*; it does not restate its sub-notes verbatim. Titles are crisp and not folder-prefixed. |
| 10 | **Frontmatter & mechanics** | 6 | Valid frontmatter with the required keys (deterministic). `sources` reflects every ingest that touched the note. No `---` blocks or `tags:` lines leaked into bodies. |

## Phase gate

After scoring, recommend **go / no-go** for the next phase:

- **Go** if: no hard failures, score ≥ 60, and no single category below 40% of
  its weight.
- **Marginal** (recommend, let the user decide) if: score 45–60, or one
  category badly missed but the rest solid.
- **No-go** if: a hard failure, or score < 45, or grounding (cat 1) below half —
  a model that hallucinates or can't survey a medium doc will only get worse on
  large ones. Running phase 3 costs the most and teaches the least in that case.

Record the phase score, the per-category notes, the check.mjs JSON, and the
timings/tokens in the phase report. The cross-phase comparison table
(`README.md`) is what actually answers "which model".
