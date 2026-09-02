# Gold reference — after phase 1

Phase 1 ingests the six small docs in `corpus/phase-1-small/` into an empty vault
(schema only). This is the reference for rubric scoring — it is **not**
byte-exact; models phrase and split differently. Score against the intent.

## Expected shape

**5–7 notes.** Fewer than 5 means under-fragmentation (two distinct topics
crammed together); more than 8 means the model is writing one note per section.

Roughly:

| note (folder + rough title) | from | covers |
|---|---|---|
| `practices/` — code review | code-review.md | purpose, what reviewers weigh, one-working-day turnaround, ~300-line size, what an approval means, review skipped only by pairing |
| `practices/` — test pyramid / testing | test-strategy-and-the-pyramid.md | unit/integration/e2e, push tests down, no duplicated coverage, flaky-test policy |
| `process/` — trunk-based development | trunk-based-development.md | single `main`, short-lived branches (**two working days** at this phase), main always releasable, incomplete work behind flags |
| `practices/` — definition of done | definition-of-done.md | done = in production and verified; the checklist; follow-ups tracked not promised; no partial |
| `practices/` — pair programming | pair-programming.md | when to pair, pair == review, rotation, not mandatory |
| `practices/` — commit conventions | commit-conventions.md | imperative summary + why in body, one logical change, referencing work, what not to commit |

## Key relationships

- **code review ↔ pair programming** must link both ways: both state "pairing
  counts as review". A model that writes the same paragraph into both notes
  without linking loses consolidation points. Merging them into one note is
  acceptable if the result is coherent.
- **definition of done** should link to the review note and the testing note (it
  references both).
- **commit conventions** may link to trunk-based development.

## Folders

Only `practices/` and `process/` are used. **`architecture/`,
`infrastructure/`, and `principles/` must be empty** — there is no content for
them yet. A note filed in `principles/` at this phase is a grounding failure
(the model inventing a principles doc).

## Traps active at this phase

- **Size gradient baseline.** These are small; survey and organize should be
  quick and cheap. Record the tokens/timings as the floor.
- No contradiction yet (trunk-based says two days; nothing disputes it).
- No cross-ingest consolidation yet (all six are fresh topics).

## Common failure signatures

- A `principles/manifesto.md` or similar invented note → hallucination.
- 10+ notes → per-section fragmentation.
- code-review and pair-programming both containing the full "pairing is review"
  text with no link → weak consolidation.
- Folder-prefixed titles (`practices/practices code review.md`).
