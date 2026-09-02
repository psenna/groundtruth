# Delivery and release management

How a change becomes something a user experiences, at the scale of many teams
shipping many times a day. This document builds on the branching, pull-request,
and pipeline practices and is the authority where they overlap.

## The shape of delivery

Delivery is a single flow with no branches in it: an engineer merges a small
change to the trunk, the pipeline builds one artefact, and that artefact
progresses automatically through staging and into production. There is no
release branch, no release candidate, no cut, no code freeze, and no release
manager. "Release" is not an event; it is the steady-state behaviour of the
system.

This only works because every other practice supports it. The trunk is always
releasable because CI is always green on it. Changes are small because branches
are short-lived — **a feature branch merges within one working day**, and this
holds no matter how large the overall feature is, because the feature is
delivered as a sequence of small merges rather than one big one. Incomplete work
is safe on the trunk because it is hidden behind flags.

## Feature flags

A feature flag decouples *deploying* code from *releasing* behaviour. The code
for a feature ships to production, dark, as it is written — in small, reviewed,
tested pieces. When the feature is complete and has been exercised in production
with the flag on for the team only, it is turned on progressively for users.

Rules for flags:

- A flag is a short-lived thing. It exists to manage one rollout. Once the
  feature is fully on and stable, the flag and the old code path are removed —
  within a few weeks, tracked as an action item. A codebase full of stale flags
  is a codebase where no path is really tested, because every flag doubles the
  number of combinations.
- A flag defaults to off and fails to off. If the flag system is unreachable,
  the code behaves as though every flag is off — the safe, known state.
- Flag changes are audited: who turned what on or off, when, and for whom.
- A flag is not a config knob. Long-lived behavioural configuration —
  "this customer gets the higher rate limit" — is configuration, versioned and
  reviewed, not a flag toggled in a UI.

## Progressive delivery

No change reaches all users at once. The deploy to production shifts a small
fraction of traffic to the new version, holds while the key metrics — error
rate, latency, and the service's domain signals — are compared against the old
version, and then either advances through larger fractions to 100% or rolls back
automatically on a regression. The whole process is minutes for a normal change.

A feature turned on via a flag follows the same shape at the flag layer: on for
the team, then a small percentage of users, then a ramp, with the same metrics
watched at each step and the same automatic halt.

## Rollback and forward-fix

Rollback is always the first response to a bad release and it is always
available. Because the pipeline builds one immutable artefact per commit, "roll
back" is unambiguous: redeploy the previous artefact. Target time is under a
minute and it requires no judgement about the cause.

Forward-fix — shipping a new change that corrects the problem — is a choice made
*after* the rollback has stopped the bleeding, calmly, through the normal
pipeline. Forward-fixing while users are affected, under pressure, skipping the
normal checks, is how a small incident becomes a large one.

A rollback is blameless and requires no permission. Anyone who sees a release
going wrong rolls it back and starts the conversation afterward.

## Database and other irreversible changes

Some changes cannot be rolled back by redeploying an artefact: a schema
migration that dropped a column, a message published to a queue, an email sent.
These are handled by never putting the irreversible step and the code that
depends on it in the same release.

A schema change is a sequence: first a release that makes the schema change in a
backward-compatible way (add the column, add the table) while the code ignores
it; then a release that starts using it; then, much later, a release that
removes the old path; then, later still, a cleanup that drops the now-unused old
column. Each step is independently deployable and independently reversible. A
migration whose *down* step would lose data that the *up* step created is not
allowed through the pipeline — the destructive cleanup is a separate, deliberate,
much-later step.

## Coordinating across services

When a change spans two services — the provider adds a capability, the consumer
uses it — the releases are still independent and still ordered by
compatibility, never by a synchronised deploy. The provider ships the new
capability first, backward-compatibly, and its contract tests prove it has not
broken existing consumers. Then the consumer ships the code that uses it. If the
consumer is ready first, it waits; it does not force a coordinated release
window. A change that genuinely requires two services to deploy in the same
instant is a design smell pointing at a boundary in the wrong place.

## Communicating releases

Because releases are continuous, we do not announce each one. What we communicate
is *user-visible behaviour changes*, and that communication is tied to the flag
flip, not the deploy. A changelog entry, a note to support, a heads-up to
affected customers — these happen when the behaviour turns on for users, which
may be days or weeks after the code shipped.

## Handling a release that needs a data backfill

Some features need existing data reshaped before they can turn on — a new column
populated for every historical row, a denormalised field computed, a set of
records reclassified. A backfill is run as its own controlled operation,
separate from the code release, and it follows rules of its own: it is
idempotent so it can be re-run after a failure; it is chunked and rate-limited so
it does not overwhelm the database or starve live traffic; it reports progress so
its state is observable; and it is reversible or, where it genuinely is not, it
is reviewed with the same seriousness as a destructive migration. The feature
flag for the new behaviour is not flipped on until the backfill has completed and
been verified. A backfill kicked off by hand from someone's laptop, unlogged and
unrestartable, is how a routine feature launch turns into a database incident.

## Time-sensitive and embargoed releases

Occasionally a behaviour change must go live at a specific moment — a partner
announcement, a regulatory deadline, a coordinated launch. This is handled purely
at the flag layer: the code ships and bakes in production dark, well ahead of
time, and the flag is flipped at the appointed moment. We do not hold code out of
the trunk to hit a date, and we do not do a special deploy at midnight. If the
flag flip itself needs to be precise, it is scheduled through the flag system's
own scheduling rather than a human watching a clock. An embargoed change still
goes through normal review; the reviewers are simply told it is sensitive and to
keep the discussion in the private channel.

## Metrics for delivery itself

We watch four numbers for the delivery process, because a delivery process that
degrades silently will not be noticed until an incident: how often we deploy
(higher is healthier, it means changes are small), how long from merge to
production (minutes, and creeping up means a pipeline problem), what fraction of
deploys trigger a rollback or a hotfix (low, and a spike means quality is
slipping upstream), and how long to recover when a deploy does go bad (minutes,
because rollback is fast and always available). These are reviewed monthly and a
regression in any of them is investigated like a production issue.
