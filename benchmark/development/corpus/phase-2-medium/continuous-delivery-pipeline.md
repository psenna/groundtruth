# The continuous delivery pipeline

Every merge to `main` runs the same pipeline, and a green pipeline deploys to
production with no human in the loop. This document describes the stages and the
rules that keep an automated deploy safe.

## Stages

1. **Build.** The artefact is built once, from the merge commit, and that same
   artefact is what eventually runs in production. We never rebuild per
   environment — a rebuild is a chance for the environments to diverge.
2. **Static checks.** Formatting, linting, type checking, and a dependency
   vulnerability scan. These are fast and they fail the pipeline hard.
3. **Unit tests.** The bulk of the suite, run in parallel. Target wall-clock
   under five minutes; when it creeps over, we shard further rather than accept a
   slow signal.
4. **Integration tests.** Real database, real message broker, brought up as
   throwaway containers. Slower, fewer, still blocking.
5. **Deploy to staging.** The artefact is deployed to an environment that is
   configured as close to production as we can afford.
6. **Smoke tests.** A thin end-to-end layer runs against staging to prove the
   critical paths are connected.
7. **Deploy to production.** Progressive: the new version takes a small share of
   traffic first, key metrics are watched for a few minutes, and the rollout
   either continues to 100% or rolls back automatically.

## Gates

The pipeline has exactly two kinds of gate: automated checks that must pass, and
the progressive-rollout health check. There is no manual approval step and no
"release manager" sign-off. A change that a human needs to think hard about
before it ships is a change that needs a feature flag, not a gate.

## Rollback

Rollback is redeploying the previous artefact, and it is always available and
always fast — sub-minute is the target. Because the build is immutable and
per-commit, "the previous artefact" is unambiguous. Forward-fixing is a choice
made calmly after a rollback, never the thing standing between users and a
working system.

## Database changes

Schema changes ship separately from and ahead of the code that needs them, in a
backward-compatible step: add the column, deploy code that can use it or not,
then later remove the old path. A migration that a rollback cannot survive is
not allowed through the pipeline.

## Failed pipelines

A red pipeline on `main` is a stop-the-line event. The team's attention goes to
making `main` green again before new work merges on top. The usual first move is
to revert the change that broke it.

## Ownership

The pipeline configuration lives in the same repository as the code it ships and
changes to it go through the same review and the same pipeline. There is no
separate "CI team" that owns it; the team that ships through it owns it.
