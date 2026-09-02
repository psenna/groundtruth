# Architecture decision records

This document describes how we make, record, and revisit architecture decisions.
It is long because the process is the cheap part and getting it wrong is
expensive: a bad architecture decision is often invisible for a year and then
impossible to reverse.

## Why we write decisions down

An architecture decision is one that is costly to change later: a choice of
data model, a service boundary, a synchronous-versus-asynchronous integration, a
build-versus-buy, a protocol, a consistency model. These decisions are made by a
few people in a room (or a thread) at a particular moment, with particular
constraints and particular knowledge. Six months later the constraints have
moved, the people have moved teams, and someone is looking at the result asking
"why on earth is it like this".

An architecture decision record — an ADR — is a short document that captures the
decision *and its context* at the moment it was made, so that the later reader
can tell the difference between "this was a considered trade-off that is still
valid", "this was right then and is wrong now", and "this was never thought
about at all". Those three cases call for very different responses, and without
the record they are indistinguishable.

We do not write an ADR for every decision. The test is reversibility: if you
could undo this in an afternoon with a small pull request, it is not an ADR, it
is just code. If undoing it would be a project, write the ADR.

## The format

An ADR is one Markdown file. It is short — one to two pages. Longer than that and
nobody reads it, and the discipline of being short forces clarity. It has these
sections:

**Title.** A short noun phrase naming the decision, prefixed with a number in
sequence. "Use cursor pagination for all list endpoints", not "Pagination".

**Status.** One of: proposed, accepted, superseded, deprecated. A superseded ADR
links forward to the one that replaced it. We never delete an ADR — a wrong
decision that was made and reversed is exactly the history a future reader
needs.

**Context.** The forces at play: the requirement, the constraints, the things we
knew and the things we did not, the options that were genuinely considered. This
is the most important section and the one most often skipped. A reader who
disagrees with the decision should be able to see whether they are disagreeing
with the reasoning or just have different information.

**Decision.** What we are actually going to do, stated plainly and in the active
voice. "We will..." not "It is recommended that...".

**Consequences.** What becomes easier and what becomes harder. Every real
architecture decision makes something worse; an ADR that lists only benefits is
not being honest and will not be trusted. Include the consequences that are
acceptable now but might not be forever.

## Where ADRs live

ADRs are stored in the repository of the system they concern, as plain Markdown
files, in a folder set aside for them. They are versioned with the code, they go
through the same review, and a change to an ADR's status is itself a small pull
request. Keeping them next to the code — rather than in a wiki — means they are
found by people reading the code, they are reviewed like code, and they cannot
rot silently while the wiki nobody visits fills up with stale pages.

There is one index that lists every ADR with its title and status, generated
from the files so it cannot drift.

## The process

1. **Someone drafts.** Anyone can write an ADR. It starts in status "proposed".
   The draft is a pull request like any other.

2. **The right people review.** "The right people" are whoever will have to live
   with the consequences: the team that owns the system, anyone whose system
   integrates with it, and someone with enough breadth to spot a decision that
   conflicts with one made elsewhere. This is not a committee and it is not a
   sign-off from a title; it is the people with skin in the game.

3. **Disagreement is resolved in the document.** If the review surfaces a real
   objection, the fix is to improve the Context and Consequences sections until
   the objection is either answered or acknowledged as a known cost. "We
   discussed this in a meeting" is not a resolution; the document has to stand
   on its own.

4. **It is accepted or it is not.** An accepted ADR is merged with status
   "accepted". A proposal that cannot reach agreement is merged anyway, with
   status "proposed" and a note on what is blocking it, so the attempt is not
   lost. A proposal that is rejected is merged with a short "rejected because"
   and closed — again, so the next person who has the same idea can find out
   why it did not fly.

5. **It is revisited when the context changes.** An ADR is not permanent. When
   someone believes a decision no longer fits, they write a new ADR that
   supersedes it — with its own Context explaining what changed — rather than
   editing the old one. The old one keeps its original text and gets a
   "superseded by" link.

## Anti-patterns

**The retroactive ADR.** Writing the ADR after the code is merged, to satisfy a
process checkbox. The value of an ADR is in shaping the decision; a retroactive
one is archaeology. If a big decision got made without an ADR, the fix is a
lightweight ADR written honestly ("this was decided in implementation, here is
the reasoning as best we can reconstruct it") plus a look at why the process was
skipped.

**The novel.** A fifteen-page ADR with a literature review. Nobody reads it, so
it does not do its job. If the decision genuinely needs that much analysis, the
analysis is an appendix or a linked document and the ADR itself is still two
pages.

**The decision with no alternatives.** A Context section that describes one
option is not describing a decision, it is describing a foregone conclusion.
Every ADR names at least one real alternative and says why it lost. If there
genuinely was only one option, it was not an architecture decision.

**The rubber stamp.** An ADR that goes up and is approved in an hour with no
comments was either trivial (and should not have been an ADR) or was not really
reviewed. Real architecture review produces questions.

**The orphan.** An ADR for a system that later gets absorbed or deleted, left
sitting in a repository that no longer exists. The index is regenerated
regularly and orphaned ADRs are moved to an archive with a tombstone.

## Worked examples

**Choosing cursor pagination.** The Context recorded that our list endpoints
were returning inconsistent pages when data changed mid-scroll, that offset
pagination degrades on large offsets, and that clients had asked for a stable
way to resume. The alternatives considered were keeping offset pagination with a
snapshot, and a "give me everything since timestamp T" feed. The Decision was
opaque cursors on every list endpoint. The Consequences section was honest that
cursors cannot jump to "page 50", that the cursor encoding is now a compatibility
surface we must not break, and that existing offset-based clients need a
migration window. Two years later a reader can see this was a considered
trade-off, still valid, and does not need to relitigate it.

**Choosing synchronous calls for the checkout path.** The Context recorded a
requirement that a failed payment must be visible to the user immediately, ruling
out a fire-and-forget queue. The alternative — an asynchronous call with a
websocket push for the result — was rejected as more moving parts for a
sub-second operation. The Consequences section noted that this couples checkout's
availability to the payment service's availability, and set a follow-up to add a
circuit breaker. When the payment service had an outage a year later, the
postmortem could point at the ADR: the coupling was known, accepted, and
mitigated as planned.

**A superseded decision.** An early ADR chose a shared database for two services
that were expected to stay tightly related. Eighteen months later they had
diverged, and a change to one kept breaking the other. A new ADR superseded it:
its Context explained what had changed (the services now served different
teams on different schedules), its Decision was to give each its own store
behind its own API, and its Consequences accepted a data-migration project as
the cost. The original ADR kept its text and gained a "superseded by" link — the
history shows the shared database was reasonable when chosen and wrong later,
which is different from "someone made a bad call".

## Relationship to other decisions

Some decisions look like architecture but are really policy — how we branch, how
we review, how we run incidents. Those are documented as practice and process
notes, not ADRs, because they are reversible and they are about how people work
rather than how the system is shaped. When a process decision and an
architecture decision interact — for example, a choice to split a service
changes who is on call for it — the ADR notes the process consequence and links
to the relevant process note rather than restating it.

## Reviewing an ADR you disagree with

The most valuable review is a dissenting one, and it has a shape. First, work out
whether you disagree with the *reasoning* or with the *information*: if the
Context is missing a constraint you know about, that is a factual fix and the
document gets better. If you have the same facts and would still decide
differently, say so explicitly, state your alternative, and state what it would
cost — then let the responsible people decide. What you do not do is approve with
a comment like "not how I'd do it but okay"; that leaves no record of the
objection and no record of why it was overruled. A dissent that is written down
and then not taken is still valuable: the next person to hit the consequences can
find it.

## The cost of this process

It is not free. A good ADR takes a few hours to write and a review cycle to
land, and there is a standing temptation to skip it for a decision that "is
obviously right". The mitigation is the reversibility test: most decisions do
not need an ADR, and being strict about that keeps the ones that do get written
seriously. A team that writes forty ADRs a year is writing too many; a team that
writes zero is not making its expensive decisions visible.
