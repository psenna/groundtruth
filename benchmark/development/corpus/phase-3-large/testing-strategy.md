# Testing strategy

This is the complete version of how we test. The short "test pyramid" note is a
summary of the first section; where they differ, this document is correct.

## The pyramid, in full

The pyramid is a statement about *proportion and cost*. Tests near the bottom are
numerous, fast, and cheap to maintain; tests near the top are few, slow, and
expensive. The reason to keep the shape is that the maintenance cost of a test
suite is dominated by its slow and flaky tests, and those cluster at the top.

**Unit tests** are the foundation. A unit test exercises one unit of behaviour —
often one function or one class, sometimes a small cluster of them — with its
external collaborators replaced by fakes or stubs at the seam. It does no I/O: no
network, no disk, no real database, no real clock, no sleeping. It runs in
single-digit milliseconds. The suite of them runs in a few minutes even when
there are tens of thousands, because they parallelise perfectly.

Unit tests are where behaviour is specified. If there is a rule — "an order over
the limit is rejected", "a retry backs off" — there is a unit test that pins it,
and that test was watched failing before the code that satisfies it was written.
A rule that is only checked by a slower test higher up is a rule that is
under-tested, because the feedback is too slow to shape the design.

**Integration tests** sit above. An integration test uses real infrastructure —
a real database in a throwaway container, the real HTTP router, a real message
broker — for a small, deliberately chosen slice of the system. They are an order
of magnitude fewer than the unit tests and an order of magnitude slower. Their
job is narrow: catch the mistakes that fakes hide. A fake repository always
returns what you told it to; a real database has constraints, transactions,
serialisation, and its own opinion about your query. Integration tests find the
gap between what you assumed the collaborator does and what it actually does.

An integration test that is really just re-checking business logic that a unit
test already covers is deleted. The rule is: an integration test earns its place
only by exercising a real boundary.

**End-to-end tests** are the thin cap. They drive the deployed system from the
outside — through its public API or its UI — with everything real. They are
slow, they are the most likely to flake, and they are the most expensive to
debug when they fail because the failure could be anywhere. We keep only enough
to answer one question: are the critical user journeys wired together and
working. Perhaps a dozen of them for a whole product. They assert on outcomes
("the order appears in the history"), never on internal details.

## Test data

Test data is a first-class concern because bad test data is the top cause of
flaky and unmaintainable tests.

Unit tests build the data they need inline, in the test, using small builder
helpers that fill in sensible defaults so each test states only the fields it
cares about. There are no shared fixtures loaded from files — a shared fixture
becomes a coupling point that every test is afraid to change.

Integration tests set up their world at the start of the test and tear it down
after, each test owning its own data, so that tests do not depend on order and
can run in parallel. A test that only passes when run after another test is a
bug in the test.

End-to-end tests run against an environment seeded with a known baseline and
create anything else they need through the real API, then clean up. They never
assume the environment is empty and never leave it dirty.

## Contract tests

Where two of our services integrate, we use contract tests instead of a shared
end-to-end test that spins up both. The consumer writes a test that states
exactly what it sends and what shape of response it depends on; that expectation
is published; and the provider runs a test that verifies it still satisfies
every published consumer expectation. When the provider wants to make a change,
its own CI tells it which consumers would break. This keeps the two services
independently deployable, which a shared end-to-end test would quietly destroy.

## Flakiness

A flaky test — one that passes and fails without the code changing — is treated
as a production incident of its own kind, because its real damage is cultural: a
suite that is sometimes red for no reason trains the whole team to click "merge"
through a red build, and then a real failure sails through.

The policy: a test observed to flake is quarantined out of the blocking suite
within one working day, with a tracked item to fix or delete it within one week.
We do not "retry three times and pass" in CI — a retry that hides a flake also
hides a real intermittent bug. If the flake cannot be fixed in a week, the test
is deleted and the gap in coverage is noted; a deleted flaky test is better than
a retained one.

## What we do not do

**We do not chase a coverage number.** Line coverage measures which lines ran,
not which behaviours are pinned. A module at 95% coverage with no assertions is
worse than one at 70% with sharp ones. We look at coverage to find *surprising
gaps* — a whole error path with nothing exercising it — not to hit a target.

**We do not write tests after the fact to "catch up".** A test written against
code that already works, without being seen to fail, proves only that it agrees
with the current implementation, bugs included.

**We do not mock our own code deep inside a unit test.** Mocking is for the seam
at the edge of the unit. A test that mocks three layers of our own classes is
testing the mocks.

**We do not keep tests that never fail.** A test that has not failed in a year,
through many real changes to the code it covers, is either duplicating other
coverage or asserting nothing. It is reviewed and usually removed.

## Testing non-functional requirements

Correctness is not the only thing tests protect. Where a non-functional property
matters, it is pinned by an automated check that fails when the property
regresses.

**Performance.** Hot paths have a benchmark that runs in CI and fails on a
significant regression against a recorded baseline. This is not a load test — it
is a microbenchmark of a specific operation — and its job is to catch the
accidental O(n squared) or the extra database round-trip before it ships.

**Security.** The dependency scanner runs on every build and fails on a known
vulnerability above a threshold. Static analysis for the common injection and
auth-bypass classes runs in the same stage. Neither replaces a real security
review for sensitive changes, but both catch the regressions that a review would
be embarrassed to miss.

**Backward compatibility.** For any public contract — an API, an event schema, a
stored data format — a test loads a corpus of real historical payloads and
asserts the current code still accepts them. This is how "we tolerate unknown
fields" stops being a slogan and becomes a guarantee.

**Accessibility and other product properties** are tested at the level they live
at, usually a focused integration or end-to-end check, and they block the same
way a correctness failure does.

## When a bug escapes

Every bug that reaches production and is worth fixing gets a failing test first —
one that reproduces the bug and fails against the released code — before the fix
is written. That test is written at the lowest level that can express the bug.
This does two things: it proves the fix actually addresses the reported problem,
and it permanently pins the behaviour so the same bug cannot return. A fix that
ships without a test that would have caught the bug is an incomplete fix, and in
review that is a blocking comment.

The postmortem for a significant escape asks a specific question: what kind of
test would have caught this, and why did we not have it. The answer feeds back
into the strategy — sometimes it is a missing contract test, sometimes a whole
class of input we never fuzzed, sometimes an integration boundary we were faking
when we should have been exercising.

## Tests in the pipeline

Static checks and unit tests run on every push and block the merge. Integration
tests run on every merge to the trunk and block the deploy. End-to-end smoke
tests run against staging and block the promotion to production. The wall-clock
budget for the blocking unit suite is five minutes; when it exceeds that we shard
it further rather than move tests out of the blocking set. A slow test suite is a
tax on every change, and teams route around a tax.
