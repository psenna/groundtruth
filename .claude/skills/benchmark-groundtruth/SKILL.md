---
name: benchmark-groundtruth
description: Run the phased software-development benchmark in benchmark/development/ against a groundtruth instance to evaluate how well an LLM drives ingestion and recovery. Use when asked to "run the benchmark", "benchmark model X", "score groundtruth on model X", or to compare models. Drives phases 1→2→3 (small→medium→large corpus), runs deterministic checks + rubric scoring against gold/ after each phase, and stops at each gate for a go/no-go decision.
---

# benchmark-groundtruth

Runs `benchmark/development/` end to end: a size-graded, phase-gated evaluation of
how well the currently-configured model drives groundtruth's ingest and query
pipelines. Read `benchmark/development/README.md` and `rubric.md` first.

## Inputs (`args`)

- `--label <slug>` — short model tag; the vault is `bench-<slug>` (e.g.
  `--label q38` → `bench-q38`). **Required.** Ask if not given.
- `--target <url|local>` — where groundtruth runs.
  - a URL → use it directly.
  - `local` → bring up a local instance (see "Local target" below).
  - **omitted** → use the target URL already established in this session if there
    is one; otherwise **ask the user for the deployed URL** (do not assume).
- `--from-phase <1|2|3>` — resume at a phase (the vault must already hold the
  earlier phases). Default 1.
- `--only-phase <1|2|3>` — run exactly one phase and stop.

The model is **not** a parameter — whichever model the target's config points at
is the one under test. Before phase 1, confirm with the user that the target is
configured for the intended model (all four roles) and, for a local
reasoning-capable model, `reasoning_effort: none`.

## Setup (once per run)

1. Resolve `--target` per the rules above. `curl -s <target>/health` must return
   `{"status":"ok"}`.
2. If `--from-phase` is 1: create the vault and set the schema.
   ```sh
   node benchmark/development/scripts/bench.mjs create <target> bench-<slug>
   node benchmark/development/scripts/bench.mjs schema <target> bench-<slug>
   ```
   `schema` uses the MCP `update_schema` tool — the target must have
   `allow_schema_writes: true` (the eval instances do). Verify:
   `curl -s <target>/schema/bench-<slug>` shows the 5-folder schema.
3. If `--from-phase` > 1: verify the vault exists and already has the earlier
   phases' notes (`bench.mjs jobs`). Do not re-run earlier phases.

## Per phase (loop 1 → 2 → 3, honouring `--from-phase` / `--only-phase`)

### 1. Ingest

```sh
node benchmark/development/scripts/bench.mjs ingest <target> bench-<slug> <N>
# phase 2 ONLY — also fire the dedup probe:
[ <N> = 2 ] && node benchmark/development/scripts/bench.mjs dedup <target> bench-<slug>
node benchmark/development/scripts/bench.mjs wait <target> bench-<slug>
```

`ingest` submits every doc in `corpus/phase-<N>-*/` asynchronously; the queue is
per-vault serial. `wait` polls until nothing is queued or running. A dense/slow
model can take 30–90 min for phase 3 — run `wait` with `run_in_background: true`
and pick it up on the completion notification.

**Do not delete or reset the vault between phases** — phases are cumulative; that
is how consolidation and the §7.5 contradiction get tested.

### 2. Deterministic checks

```sh
node benchmark/development/scripts/check.mjs --target <target> --vault bench-<slug> \
  --phase <N> --json /tmp/bench-<slug>-p<N>.json
```

`check.mjs` exits non-zero and prints `HARD FAILURES` when the run is not worth
scoring: dangling wikilinks, placeholder/heading-only notes, undeclared folders,
an out-of-scope query answered instead of refused, or ingest success < 60%.

- **Hard failure → the phase scores 0. STOP the run.** Report what failed and
  why. Do not proceed to later phases.
- Otherwise → continue to scoring.

### 3. Rubric scoring

```sh
node benchmark/development/scripts/bench.mjs dump <target> bench-<slug> > /tmp/bench-<slug>-p<N>-notes.txt
```

Read the note dump, the `check.mjs` JSON (esp. the `queries.rows` — the answer
text), and `benchmark/development/gold/after-phase-<N>.md`. Score the 10
categories in `benchmark/development/rubric.md` out of 100. Be specific in the
per-category notes — cite note paths.

For consistency across model runs you MAY delegate step 3 to one subagent
(Opus, `general-purpose` or `feature-dev:code-reviewer`) given the dump + JSON +
the gold file + `rubric.md`, and relay its scored report. One subagent only.

### 4. Phase report + gate

Write a phase report:

```
## Phase <N> — bench-<slug> (<model>)
- ingests: X/Y ok, <dedup> dedup, failures: ...
- timings: survey Ns, llm Ns, ...   tokens: survey N, organize N, ...
- check.mjs: <hard failures / clean> ; flags: ...
- rubric: <score>/100
  1 grounding N/18 — ...
  ... (all 10)
- queries: kind K/T, refusals R/Rt, grounded+cited C/Gt
- gate recommendation: GO | MARGINAL | NO-GO — <one sentence why>
```

Apply the gate rule from `rubric.md` (go ≥ 60 & no category < 40% weight;
no-go < 45 or grounding < half or any hard failure; marginal between).

**Present the report and the recommendation to the user and STOP.** The user
decides whether to run the next phase. Do not auto-continue.

## After the last phase run

Emit the cross-model comparison row for `README.md`'s table:

| model | P1 | P2 | P3 | P1→P3 wall | survey tok (P3) | verdict |

and a 2–3 sentence verdict (quality vs. speed vs. where it broke).

## Local target

`--target local` when the user wants a self-contained run:

1. Model endpoint: the container needs to reach an OpenAI-compatible LLM. Ask the
   user for the `base_url` and model, or reuse the deployed instance's.
2. Bring up groundtruth via Docker (see the `use-docker` skill for the DinD
   pattern). Minimal `config.yaml`: `auth: none`, one vault path, all four
   `models.*` roles set to the target model, `allow_schema_writes: true`,
   `limits.max_tool_calls: 40`, `max_wall_clock_s: 900`, `llm_timeout_s: 600`.
   Mount `/data` and the state dir as volumes.
3. `--target http://<container-host>:8000` from then on. Tear the container down
   when the run is done; keep the volumes if the user wants the vault.

## Guardrails

- Never edit the corpus, `gold/`, `queries.md`, or `rubric.md` to make a run look
  better. If a corpus doc has a real bug, fix it in its own commit and note that
  prior results are not comparable.
- The benchmark measures the **model**, not groundtruth — but a hard failure that
  reproduces across several capable models is a groundtruth bug; record it in the
  report and (if asked) file it.
- One rubric-scoring subagent maximum. No parallel phase runs.
