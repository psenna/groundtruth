# Definition of done

A piece of work is "done" when it is in production and behaving as intended — not
when the code is written, not when the pull request is open, not when it is
merged. This shared definition exists so that "done" means the same thing in
standup as it does on the board.

## The checklist

A change is done when all of the following are true:

- The behaviour is covered by automated tests at the lowest useful level, and
  those tests were seen to fail before the code made them pass.
- It has been reviewed, or written as a pair.
- It is merged to `main` and has passed CI there.
- It has been deployed to production through the normal pipeline.
- Any user-facing change has been checked in production by the author.
- Any operational change — a new metric, a new alert, a new runbook entry — has
  been made, not just noted as a follow-up.
- Documentation that a reader would expect to be current is current.

## Follow-ups

"I'll do the tests / the alert / the docs in a follow-up PR" is how those things
never happen. A follow-up is acceptable only for genuinely separable work, and it
is filed as a tracked item before the first change merges, not promised in a
review comment.

## Partially done

There is no "80% done." Work that cannot be finished to this bar in its current
form is too big and should be sliced into pieces that each can.
