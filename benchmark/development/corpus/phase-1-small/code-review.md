# Code review

Every change that reaches the main branch has been read by at least one engineer
other than its author. Code review is not a gate we tolerate; it is the main way
knowledge about the system spreads across the team, and the main place we catch
design problems while they are still cheap to fix.

## What reviewers look for

Reviewers are asked to weigh four things, roughly in this order: does the change
do what it claims and is that the right thing to do; is it covered by tests that
would fail if the behaviour regressed; will another engineer understand it in six
months; and does it follow the conventions already established in that part of
the codebase. Style nits that a formatter or linter could catch are out of scope
for a human reviewer and should never block a merge.

## Turnaround

A review request should get a first response within one working day. If the
author has been waiting longer than that, escalating in the team channel is
expected, not rude. Reviews that drag on for days are usually a sign the change
is too large; see our guidance on small changes.

## Size

The single strongest predictor of a fast, useful review is a small diff. Aim for
changes under roughly 300 lines. A change that must be large — a mechanical
rename, a generated-code update — should say so in its description and point the
reviewer at the few lines that actually need judgement.

## Approving

An approval means "I would be comfortable being paged for this." A reviewer who
is not comfortable saying that should ask questions rather than approve. Blocking
a change is done sparingly and always with a concrete, actionable reason; "I
would have done this differently" is not one.

## When review can be skipped

Pairing counts as review: a change written by two engineers together does not
need a separate asynchronous review, provided the pair notes that in the
description. Nothing else skips review — not a hotfix, not a one-line config
change, not a revert.
