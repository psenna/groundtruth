# groundtruth development benchmark

A phased, size-graded benchmark for evaluating how well a given LLM drives
groundtruth's ingestion and recovery pipelines. The domain is a fictional
software-engineering handbook; the corpus is written so that a good run exercises
every property groundtruth is supposed to guarantee.

Use it to compare models ("is qwen3.8 worth the wall-clock over gemma4:26b?") and
to catch regressions in groundtruth itself.

## What it tests

| groundtruth property | how the benchmark exercises it |
|---|---|
| grounded, no model knowledge | corpus is fictional; `queries.md` asks things that are *not* in it and must be refused |
| citations / refusals | every grounded query has an expected citation target; every out-of-scope query must refuse |
| one note per topic (ADR-9) | corpus docs each span several sub-topics; the rubric checks for spray and for cram |
| no duplicate/overlapping notes | `test-strategy` (P1) vs `testing-strategy` (P3), `branching` (P2) vs `delivery` (P3) deliberately overlap |
| dedup | the skill re-ingests one P1 doc verbatim in P2 |
| "newer wins" on conflict (§7.5) | P1 says branches live 2 days; P2 says 1 day and supersedes it |
| schema / folder discipline | 5 flat folders, no sub-folders declared; the P3 ADR doc tempts `architecture/decisions/` |
| wikilink integrity | cross-references between docs; `check.mjs` verifies every link resolves |
| tag quality | schema gives prescriptive vocab (`ci-cd` not `pipeline`); rubric checks specificity + consistency |
| no placeholder notes (#110) | `check.mjs` fails on any heading-only / "TODO" / link-only body |
| scaling to real inputs | phases go 1.5 KB → 3 KB → 8–10 KB docs; weak models die on survey budget at phase 3 |
| all-or-nothing ingest (invariant 7) | `check.mjs` records which jobs failed and confirms nothing partial landed |

## Layout

```
schema.md                     the benchmark vault's schema (5 flat folders + tag vocab)
corpus/
  phase-1-small/   6 docs   ~1.3–2 KB    single-topic practice notes
  phase-2-medium/  6 docs   ~2.5–3.4 KB  process / architecture / infra topics
  phase-3-large/   5 docs   ~7.6–10 KB   comprehensive multi-section documents
queries.md                    the recovery-side test set (grounded + must-refuse)
rubric.md                     the 100-point scoring rubric + the phase-gate rule
gold/after-phase-{1,2,3}.md   reference for what the vault should look like after each phase
scripts/check.mjs             deterministic checks (facts, not judgement)
```

## Running it

Use the **`benchmark-groundtruth` skill** — it drives the whole phased flow,
stops at each gate, and produces the phase report. Manual steps below are for
reference / debugging.

### Prerequisites

- A running groundtruth (v0.0.10+) configured for the model under test — **all
  four roles** (`default`, `tag`, `reduce`, `answer`) set to that model, and for
  a local reasoning-capable model, `reasoning_effort: none`.
- `node` (no npm packages needed).
- The target URL if it is a deployed instance.

### Manual: one phase

```sh
export GT=http://<host>:<port>          # or a local compose instance
LABEL=q38                                # short model tag → vault bench-q38

# phase 1 only: create the vault + schema, then ingest phase-1
curl -s -X POST $GT/vaults -H 'content-type: application/json' \
  -d "{\"name\":\"bench-$LABEL\",\"repo_root\":\"/data/bench-$LABEL\",\"init\":true}"
# set schema via the MCP update_schema tool with the contents of schema.md
# (the skill does this; see the skill for the JSON-RPC call)

for f in corpus/phase-1-small/*.md; do
  jq -Rs --arg v "bench-$LABEL" --arg s "phase1/$(basename $f)" \
    '{vault:$v,text:.,source_label:$s}' "$f" \
  | curl -s -X POST "$GT/ingest" -H 'content-type: application/json' -d @-
done
# wait for the queue to drain (poll GET /jobs), then:

node scripts/check.mjs --target $GT --vault bench-$LABEL --phase 1 --json phase1.json
```

Then score `rubric.md` against `gold/after-phase-1.md` and the note dump.

### Phases are cumulative

Phase 2 ingests **into the same vault** as phase 1 (that is how consolidation and
the contradiction get tested). Phase 3 into the same vault again. Do **not** wipe
the vault between phases. Use a fresh vault (`bench-<label>`) only when starting a
new model.

## Evaluating a phase

1. **`check.mjs` first.** If it reports a **hard failure** — dangling links,
   placeholder notes, undeclared folders, an out-of-scope question answered, or
   ingest success < 60% — the phase scores 0 and the run **stops**. Record why.
2. **Rubric.** No hard failure → score the 10 categories in `rubric.md` (out of
   100) by reading the full note dump + the `queries` rows against
   `gold/after-phase-<N>.md`.
3. **Phase gate.** Apply the go / marginal / no-go rule in `rubric.md`. The skill
   presents this recommendation; a human decides whether to run the next phase.
4. **Record** the score, per-category notes, `check.mjs` JSON, and the
   timings/tokens in a phase report.

## Reading the result

The deliverable is a table across models:

| model | P1 score | P2 score | P3 score | P1→P3 time | survey tokens (P3) | verdict |
|---|---|---|---|---|---|---|

A model that scores well on P1/P2 but craters or budget-exhausts on P3 is a
"small inputs only" model. A model that is slow but scores 75+ on P3 is the
quality pick for batch ingestion. The phase gate exists so you stop paying for
phase 3 on a model that already showed it can't do phase 2.
