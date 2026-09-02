# Incident response

An incident is any unplanned event that is degrading the service for users right
now, or is about to. This document is what we do about one. It is deliberately
short, because the middle of an incident is not the time to read.

## Severities

- **SEV1** — a core user journey is broken or data is at risk. All hands,
  paged immediately, updates every 30 minutes, executive awareness.
- **SEV2** — a significant feature is degraded or a SEV1 is imminent. The
  owning team plus the on-call lead, updates hourly.
- **SEV3** — a minor or contained problem. Handled by the owning team in
  business hours, tracked but not paged.

When in doubt, declare the higher severity. It is cheap to downgrade a SEV1 and
expensive to have run a SEV1 as a SEV3 for an hour.

## Roles

Two roles are named explicitly at the start of any SEV1 or SEV2:

- The **incident lead** runs the response. They do not debug; they coordinate,
  decide, and communicate. They are often the on-call lead, not the person who
  knows the failing system best — that person is more useful debugging.
- The **scribe** keeps a timestamped log of what was observed, what was tried,
  and what happened. This log is the raw material for the postmortem and it is
  written as the incident unfolds, not reconstructed after.

## The first moves

Stabilise before you diagnose. If a deploy went out in the last hour, roll it
back. If a dependency is failing, shed load or fail gracefully. Getting users to
a working system buys time to understand the cause calmly. A root-cause
investigation during an active SEV1 is a mistake.

## Communication

One status channel, one owner of it (the scribe or the lead). Updates go out on
the schedule for the severity even when the update is "no change, still
investigating" — silence during an incident is worse than bad news.

## Postmortems

Every SEV1 and SEV2 gets a written postmortem within three working days. It is
blameless: it describes what happened and why the system allowed it, not who
typed the command. "Human error" is never a root cause — it is a prompt to ask
why the system made that error easy and undetectable. The postmortem produces a
small number of concrete, owned, dated action items, and those items are tracked
to completion like any other work.

## What is not an incident

A known-risky change that is being watched closely, a planned maintenance
window, a single retryable error — these are normal operations. Declaring an
incident for them just trains people to ignore the word.
