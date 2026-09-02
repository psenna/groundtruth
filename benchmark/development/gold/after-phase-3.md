# Gold reference — after phase 3

Phase 3 ingests the five large docs in `corpus/phase-3-large/` into the vault
holding the phase-1 + phase-2 notes. These docs are 7–10 KB each and several
deliberately overlap earlier notes — this is the hardest phase for survey
(a large input against a ~13-note vault) and for consolidation.

## Expected shape

**13–18 notes total.** A spray run lands 22+; an under-fragmented run drops
below 12 (large docs mashed together). The platform playbook is the one doc that
may *legitimately* become two notes.

New / changed notes, roughly:

| note (folder + rough title) | from | covers |
|---|---|---|
| `architecture/` — architecture decision records | architecture-decision-records.md | reversibility test, the 5 sections, ADRs live next to code, the 5-step process, anti-patterns, worked examples, reviewing a dissent |
| `infrastructure/` — platform / environments | platform-engineering-playbook.md | three environments, IaC + state, **config vs secrets** (secret rules), capacity planning, cost as a metric, DR (recovery point/time, restore drills), access control, standard service module |
| `practices/` — testing (strategy) | testing-strategy.md | **consolidates the phase-1 test-pyramid note**: full pyramid, test data, contract tests, flakiness policy, non-functional testing, bug-escape → failing test first, tests in the pipeline |
| `process/` — delivery & release management | delivery-and-release-management.md | **consolidates with branching + pipeline notes**: no release event, feature flags (short-lived, fail-off), progressive delivery, rollback-first, backward-compatible migrations, backfills, delivery metrics |
| `principles/` — engineering principles | engineering-principles.md | the 10 ordered principles + how to use them + conflict resolution |

## Consolidation — the key checks (rubric cat 4)

1. **Testing.** After phase 1 there is a small "test pyramid" note. After phase 3
   there must **not** be two testing notes that both explain the pyramid. Correct
   outcomes: (a) the phase-1 note is updated/absorbed into one `practices/`
   testing note sourced from both docs; or (b) a tight overview note that links
   to focused sub-notes (`contract-tests`, `test-data`) with no repetition. A
   run with `test-pyramid.md` *and* `testing-strategy.md` both describing
   unit/integration/e2e is a consolidation failure.

2. **Delivery.** `branching-and-pull-requests` (P2), `continuous-delivery-pipeline`
   (P2) and `delivery-and-release-management` (P3) overlap heavily. Correct: the
   P3 note is the canonical "how delivery works" and the P2 notes are either
   updated to point to it for the overlapping parts or narrowed to their unique
   content (PR mechanics; pipeline stages). Wrong: three notes that each
   re-explain trunk-based + rollback + migrations.

3. **The branch-lifetime value** (from phase 2) must still read **one working
   day** — `delivery-and-release-management.md` restates it; this should
   reinforce, not create a third data point or a new contradiction note.

## Sub-folder trap (rubric cat 6)

`architecture-decision-records.md` says ADRs live "in a folder set aside for
them". The model must **not** create `architecture/decisions/` — it is not
declared. The ADR note goes directly in `architecture/`.

## Folders

All five folders now in use, `principles/` for the first time. Every note in a
declared folder, spelled exactly.

## Survey stress

Record survey tokens/time per job. On the big docs (ADR ~10 KB, platform
~10 KB) a weak model will spike survey token use into the hundreds of thousands
and may hit `retrieval budget exhausted`. That failure *is* a result — it means
the model can't handle real-sized inputs.

## Common failure signatures

- `test-pyramid.md` and `testing-strategy.md` both present and overlapping.
- Platform playbook fragmented into 6+ thin notes (environments, IaC, secrets,
  capacity, cost, DR each their own stub).
- `architecture/decisions/adr-process.md` → invented sub-folder.
- A `principles/` note that adds principles not in the manifesto → hallucination.
- Survey budget exhausted on the 10 KB docs → model can't scale.
- Delivery/branching/pipeline all restating rollback and migrations → weak
  consolidation.
