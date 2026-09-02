# Trunk-based development

We work on a single long-lived branch — `main` — and keep it releasable at all
times. Work happens on short-lived branches that are cut from `main` and merged
back quickly. We do not maintain long-running development or integration
branches, and we do not batch a release off a separate branch.

## Short-lived means short-lived

A feature branch should live no more than two working days before it merges. If
the work is not done in that window, it is split: merge the part that is safe and
inert behind a flag or simply unreferenced, and continue from `main`. The point
is to keep the distance between any two engineers' work small, so that merges are
boring and conflicts are rare.

## Keeping main releasable

Every commit on `main` has passed the full CI suite. A change that turns `main`
red is reverted first and investigated second — the person who can fix it
forward is often not online, and a red `main` blocks everyone.

## Why not feature branches

Long feature branches accumulate divergence. The longer a branch lives, the more
`main` has moved underneath it, and the more the eventual merge is a
risky, hard-to-review event. Small, frequent merges turn that one scary event
into a stream of trivial ones.

## Incomplete work on main

Merging incomplete work is fine and expected, as long as it is not reachable by
users. Unreferenced code, code behind a disabled flag, or a new endpoint that is
not yet routed are all acceptable ways to land work in progress.
