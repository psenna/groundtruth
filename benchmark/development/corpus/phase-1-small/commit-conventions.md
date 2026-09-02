# Commit conventions

A commit is a unit of history that someone will read later — during a bisect,
during an incident, during a review six months from now. We optimise commit
messages for that reader, not for the person writing them.

## Format

The first line is a summary under about seventy characters, written in the
imperative ("Add retry to the export job", not "Added" or "Adding"). Then a
blank line, then a body that explains *why* the change is being made and what
the alternative was. The *what* is in the diff; the body is for the context the
diff cannot show.

## One logical change per commit

A commit does one thing. A refactor and a behaviour change do not belong in the
same commit, because a reader who wants to understand the behaviour change should
not have to wade through a rename. If a branch has grown messy, tidy the history
before opening the pull request.

## Referencing work

If a change relates to a tracked item, reference it in the body, not the summary
line. If it fixes a regression, link the commit that introduced it.

## What not to commit

No commented-out code, no debug logging left enabled, no secrets or credentials
of any kind, no large generated artefacts that the build can reproduce. A commit
that adds a dependency says so in the body and says why the dependency is worth
it.
