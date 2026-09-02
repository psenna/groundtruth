# Branching and pull requests

This document is the full version of how we move a change from a working copy to
`main`. It supersedes the short "trunk-based development" summary where the two
disagree.

## The branch

Every change starts on a branch cut from the current tip of `main`. Branch names
carry the tracked-item identifier and a short slug, so that `git branch` on a
teammate's machine is legible.

A branch is short-lived. **A feature branch merges within one working day of
being cut.** This is tighter than the older "two days" guidance and it is
deliberate: a working day is enough time to land a genuine slice of work, and
anything longer means the slice was too big. If a branch is approaching the end
of a day and is not ready, the fix is to make it ready — split it, land the inert
part, and continue from a fresh branch — not to let it run to a second day.

Rebasing a branch on `main` while you work is encouraged; it keeps the eventual
merge boring. Force-pushing to your own branch to tidy history before review is
fine. Force-pushing to `main` is rejected by the server and there is no override.

## The pull request

A pull request opens when the change is ready for another person, not as a
place to park work in progress. The description carries three things: what the
change does in plain language, why it is being made, and how the author verified
it. A PR that closes a tracked item says so with a linking keyword.

### Review expectations

The reviewer contract is the one described in our code-review practice: a first
response within one working day, judgement on correctness / tests /
readability / local convention, and no blocking on anything a formatter could
fix. What this document adds is the mechanics: at least one approval from someone
other than the author is required before the merge button is enabled, and the
CI suite must be green on the merge commit, not just on the branch tip.

A change written as a pair may skip the separate approval, exactly as the
pairing practice describes, provided the PR description names both engineers.

### Size and splitting

The review practice asks for diffs under about 300 lines. When a change cannot
meet that, the author splits it into a stack of PRs that each stand alone and
each pass CI. A stacked PR names its parent in the description. We do not merge a
600-line PR because "it is all one feature" — the feature is delivered as a
sequence of small, safe merges.

## Merging

We merge by squash: the branch's history collapses to a single commit on `main`
whose message is the curated PR description. The individual work-in-progress
commits on the branch are not preserved, so the discipline of a clean summary and
a real "why" in the PR body matters — that text becomes the permanent history.

After merge, the branch is deleted automatically. A revert is a normal, blameless
action: if a merged change causes a problem, anyone reverts it immediately and
the discussion happens afterward on `main`.

## Hotfixes

There is no separate hotfix process and no hotfix branch. An urgent production
fix is a normal small branch, a normal PR, and a normal squash merge — it just
moves through review and CI faster because someone is watching it. If production
is actively on fire and the fix cannot wait for CI, the action is to roll back
the last deploy, not to bypass the pipeline.
