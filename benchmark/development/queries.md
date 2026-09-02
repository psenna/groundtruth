# Benchmark queries

The recovery-side test. `scripts/check.mjs` runs every line against `POST /query`
for the benchmark vault and checks the *kind* of response (answer vs. refusal)
and, for grounded questions, that the expected note was cited.

## Format

```
- [G] <minPhase> || <question> || expect: <note-stem>, <note-stem>
- [R] <minPhase> || <question>
```

- `[G] N` — should be **answered with a citation** once phase ≥ N has been
  ingested; should be **refused** before that (the content is not in the vault
  yet).
- `[R] 9` — **always refused**. The answer is not anywhere in the corpus, and
  groundtruth must say so rather than answer from model knowledge.
- `expect:` — one or more note stems (bare filename or `folder/stem`); the
  grounded answer must cite at least one. Optional.

The script checks kind + citation only. **Faithfulness of the answer text is
scored by the rubric**, not here — read the `answer:` fields in the JSON output.

---

## Grounded — answerable from phase 1

- [G] 1 || What are the three levels of the test pyramid? || expect: test-strategy-and-the-pyramid, testing-strategy
- [G] 1 || When can code review be skipped? || expect: code-review, pair-programming
- [G] 1 || What does a commit message body explain? || expect: commit-conventions
- [G] 1 || When is a piece of work considered done? || expect: definition-of-done
- [G] 1 || What is the rule for a flaky test? || expect: test-strategy-and-the-pyramid, testing-strategy

## Grounded — answerable from phase 2 (some update phase-1 facts)

- [G] 2 || How long can a feature branch live before it must merge? || expect: branching-and-pull-requests, delivery-and-release-management
- [G] 2 || What is the distributed monolith and why is it bad? || expect: service-boundaries
- [G] 2 || What are the two roles named at the start of a SEV1? || expect: incident-response
- [G] 2 || What pagination style do our list endpoints use, and why? || expect: api-design-guidelines
- [G] 2 || What is an error budget and what happens when it is spent? || expect: observability-strategy
- [G] 2 || Is there a manual approval step in the deployment pipeline? || expect: continuous-delivery-pipeline
- [G] 2 || What are the good reasons to split a service into two? || expect: service-boundaries

## Grounded — answerable from phase 3

- [G] 3 || What sections does an architecture decision record contain? || expect: architecture-decision-records
- [G] 3 || How are secrets delivered to a running process? || expect: platform-engineering-playbook
- [G] 3 || How many long-lived environments do we run, and what are they? || expect: platform-engineering-playbook
- [G] 3 || What is a feature flag for, and how long should one live? || expect: delivery-and-release-management
- [G] 3 || How is a database schema change rolled out safely? || expect: delivery-and-release-management, continuous-delivery-pipeline
- [G] 3 || Which engineering principle wins when two principles conflict? || expect: engineering-principles
- [G] 3 || How do we test that a fixed bug does not come back? || expect: testing-strategy
- [G] 3 || What makes a backup "real"? || expect: platform-engineering-playbook

## Always refused — not in the corpus at all

- [R] 9 || What database does the checkout service use?
- [R] 9 || Which cloud provider do we run on?
- [R] 9 || Who is the head of engineering?
- [R] 9 || What is the on-call compensation policy?
- [R] 9 || How do we handle GDPR data deletion requests?
- [R] 9 || What is the company's parental leave policy?
- [R] 9 || What programming language is the payment service written in?
- [R] 9 || How much does the platform cost per month?
- [R] 9 || What is our policy on using AI coding assistants?
