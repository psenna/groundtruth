# The test pyramid

We describe our automated tests as a pyramid: many fast tests at the bottom,
fewer slow tests at the top. The shape matters because the cost of a test —
to write, to run, and to keep passing — grows as you move up, while the
confidence each individual test gives you does not grow nearly as fast.

## The three levels

**Unit tests** exercise one module with its collaborators faked at the boundary.
They do not touch the network, the clock, the filesystem, or a real database.
They are the bulk of the suite and they run in seconds. If a unit test is hard
to write, that is usually the design talking.

**Integration tests** exercise a handful of real components together — a service
and its real database, a handler and the real router. They are slower and there
are far fewer of them. They exist to catch the wiring mistakes that unit tests
with fakes cannot see.

**End-to-end tests** drive the whole system the way a user would. They are the
slowest, the flakiest, and the most expensive to maintain, so we keep only a
thin layer of them — enough to prove the critical paths are connected, not
enough to specify behaviour. Behaviour is specified lower down.

## What to test where

Push each test as far down the pyramid as it can go while still being
meaningful. A rule that can be checked with a unit test should not have an
integration test written for it as well. Duplicated coverage at multiple levels
is a maintenance cost with no extra safety.

## Flakiness

A test that fails intermittently is worse than no test: it trains the team to
ignore red. A flaky test is quarantined within a day and either fixed or
deleted within a week. We do not retry failed tests in CI to make them pass.
