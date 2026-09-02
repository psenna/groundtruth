# Pair programming

Pairing is two engineers working on one change at one screen (or one shared
editor). We treat it as a normal way to work, not a special ceremony, and we
reach for it deliberately in a few situations rather than pairing on everything.

## When to pair

- Onboarding: a new engineer pairs with someone from the team for their first
  couple of weeks rather than picking up solo tickets.
- Unfamiliar territory: a change in a part of the system neither person knows
  well is faster and safer with two sets of eyes from the start.
- High-stakes changes: anything touching auth, billing, or data migration is
  paired by default.
- Unblocking: when someone has been stuck for more than an hour, pairing is the
  first thing to try, before a long written back-and-forth.

## Pairing and review

A change written by a pair does not need a separate asynchronous review — the
second person is the review, done continuously instead of at the end. The pull
request description must say who paired so the history is clear. This is the only
sanctioned way to merge without a separate reviewer.

## Rotation

Pairs are not fixed. Rotate the pairing partner every day or two so that
knowledge spreads across the whole team rather than pooling in one duo. The
navigator (the person not typing) should change hands within a session too.

## It is not mandatory

Most routine work is still done solo. Pairing is a tool for the situations above,
and forcing it everywhere burns people out and slows the team down.
