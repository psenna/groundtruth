# Where to draw service boundaries

Splitting a system into services is a decision with a high cost and a narrow set
of good reasons. This document is about when to split, when not to, and the
failure mode that catches teams who split for the wrong reasons.

## The default is one service

New functionality goes into an existing service unless there is a specific,
stated reason it cannot. A single well-organised service with clear internal
module boundaries is cheaper to build, test, deploy, and reason about than two
services, and it can be split later if a real boundary emerges. The reverse —
merging two services back together — almost never happens, because by then the
split has calcified into two teams, two pipelines, and two on-call rotations.

## Good reasons to split

- **Independent scaling with very different profiles.** A component that needs
  ten times the CPU per request, or that is bursty when the rest of the system
  is steady, genuinely benefits from its own deployment.
- **A hard isolation requirement.** Code that handles payment card data, or that
  runs untrusted input, is worth isolating so that a compromise is contained.
- **A different rate of change with a different team.** If two parts of the
  system are owned by two teams that release on genuinely independent schedules,
  a boundary lets them stop coordinating.
- **A different availability requirement.** A background reporting component
  should not be able to take down the request path; separating them lets the
  critical part have its own error budget.

## Bad reasons to split

- "Microservices are the standard." They are a trade-off, not a default.
- "This module is getting big." A big module is refactored, not extracted.
- "Different language." Wanting to use another language is not, by itself, a
  boundary.
- "It will be more testable." A well-designed module is just as testable in
  process, and far faster to test.

## The distributed monolith

The failure mode is a set of services that must all be deployed together, that
call each other synchronously in deep chains, and that share a database or a set
of data types so tightly that a change to one forces a change to the others.
This has every cost of a distributed system — network failures, partial
failures, latency, operational surface — and none of the benefits, because the
services are not actually independent. It is strictly worse than the monolith it
replaced.

The tell is a change that "sounds simple" but touches three repositories and
needs its deploys ordered. When you see that, the boundary is in the wrong place
or should not exist.

## If you do split

A service owns its data. No other service reads its database directly; they go
through its API. The boundary is an explicit, versioned contract, and calls
across it assume the other side can be slow or absent. A split that does not do
these things is a distributed monolith with extra steps.
