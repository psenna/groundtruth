# Gold reference — after phase 2

Phase 2 ingests the six medium docs in `corpus/phase-2-medium/` into the vault
that already holds the phase-1 notes. The skill also **re-ingests one phase-1
doc verbatim** (`commit-conventions.md`) as a dedup probe.

## Expected shape

**10–14 notes total** (the ~6 from phase 1, adjusted, plus ~5–7 new). More than
16 is fragmentation; fewer than 9 means medium docs were crammed together.

New / changed notes, roughly:

| note (folder + rough title) | from | covers |
|---|---|---|
| `process/` — branching & pull requests | branching-and-pull-requests.md | **updates the trunk-based note**: branch lifetime is now **one working day**; PR contract, squash merge, stacked PRs, no hotfix branch |
| `process/` — continuous delivery pipeline | continuous-delivery-pipeline.md | build-once, the 7 stages, two gate kinds, sub-minute rollback, backward-compatible migrations |
| `architecture/` — API design | api-design-guidelines.md | resources/methods, path-prefix versioning + tolerate-unknown-fields, uniform error shape + status codes, cursor pagination, idempotency keys, caller-set timeouts |
| `architecture/` — service boundaries | service-boundaries.md | default one service, 4 good reasons, 4 bad reasons, the distributed monolith (**tag `anti-pattern`**), a split owns its data |
| `infrastructure/` — observability | observability-strategy.md | metrics/logs/traces, SLOs + error budget, symptom-based alerting, one overview dashboard |
| `process/` — incident response | incident-response.md | SEV1/2/3, incident lead + scribe, stabilise before diagnose, comms cadence, blameless postmortem within 3 days |

## Contradiction — the key check (rubric cat 5)

`trunk-based-development.md` (phase 1) says a feature branch lives **no more than
two working days**. `branching-and-pull-requests.md` (phase 2) says **within one
working day** and explicitly calls it "tighter than the older guidance".

A correct vault after phase 2:

- states the branch lifetime as **one working day** wherever it appears,
- has the trunk-based note either **updated in place** (body now says one day,
  `sources` lists both docs) or **folded into** the branching note,
- has **no note about the two docs disagreeing**, no "note: earlier guidance
  said two days", no side-by-side.

Query `How long can a feature branch live before it must merge?` must answer
**one working day** and cite the branching note.

## Consolidation (rubric cat 4)

- **Dedup:** re-ingesting `commit-conventions.md` must produce
  `deduplicated: true` and create/change nothing. `check.mjs` reports the dedup
  count.
- **code review:** `branching-and-pull-requests.md` restates the review contract
  and adds mechanics (one approval, green CI on the merge commit). The review
  note should **gain the mechanics** or the branching note should **link to** the
  review note for the contract — not a verbatim copy of the review criteria in
  both.

## Folders

`practices/`, `process/`, `architecture/`, `infrastructure/` all now in use.
`principles/` still empty.

## Common failure signatures

- A `process/branch-lifetime-change.md` or a "the guidance was updated" note →
  botched contradiction handling.
- Branch lifetime still stated as "two days" somewhere → contradiction not
  propagated.
- Re-ingested commit-conventions produced a second note or a no-op edit that
  isn't flagged dedup → dedup broken (or the model's fault, note which).
- `architecture/patterns/distributed-monolith.md` → invented sub-folder.
- Six separate notes for the pipeline's seven stages → fragmentation.
