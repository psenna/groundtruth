# Schema

A software-engineering handbook for a fictional product organisation ("the
company"). Every note is distilled from a source document under
`benchmark/development/corpus/` and cites it. If a fact is not in an ingested
document, groundtruth must say so rather than answer from model knowledge.

## Folders

<!-- Notes may only be created in a folder listed here, EXACTLY as written.
     Put a note directly in one of these folders. Do NOT invent sub-folders
     (e.g. `architecture/patterns/` is not declared and must not be created). -->

- principles/ — cross-cutting engineering values and the reasoning behind them.
- practices/ — how individual engineers work: code review, testing, pairing,
  commit hygiene, definition of done.
- process/ — how the team works together: branching, pull requests, releases,
  planning, incident response.
- architecture/ — architecture strategy and recorded decisions: service
  boundaries, API design, data ownership, decision records and their trade-offs.
- infrastructure/ — platform and delivery strategy: environments, CI/CD,
  observability, secret management, capacity and cost, disaster recovery.

## Tags

<!-- Lowercase, dash-separated. Guidance, not an inventory — the vocabulary in
     use is derived from the notes themselves. Aim for 2–6 tags per note that
     name what the note is actually about. -->

- Area tags: `practices`, `process`, `architecture`, `infrastructure`,
  `principles`.
- Topic tags: `code-review`, `testing`, `ci-cd`, `branching`, `releases`,
  `observability`, `incidents`, `api-design`, `service-boundaries`,
  `secrets`, `capacity`, `disaster-recovery`, `feature-flags`,
  `decision-records`.
- Use `anti-pattern` for a note whose subject is a mistake to avoid.
- Prefer `ci-cd` over `pipeline`; prefer `observability` over `monitoring`;
  prefer `incidents` over `outages`.
