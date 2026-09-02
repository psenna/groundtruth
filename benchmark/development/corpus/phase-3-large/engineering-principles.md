# Engineering principles

These are the values underneath every practice, process, and architecture
decision in this handbook. When a specific rule does not obviously apply to a
situation, or two rules seem to conflict, these principles are how we decide.
They are ordered: earlier principles win ties.

## 1. Optimise for the reader, not the writer

Code is read far more often than it is written, and by more people, and under
worse conditions — during an incident, months later, by someone who has never
seen it. Every choice between "faster to write now" and "clearer to read later"
goes to the reader. This is why commit messages explain *why*, why functions are
named for what they do rather than how, why a clever one-liner loses to three
obvious ones, and why we delete code aggressively — the best code for the reader
is the code that is not there.

## 2. Prefer reversible decisions, and make irreversible ones slowly

Most decisions can be undone cheaply. Make those quickly, learn from the result,
and move on — deliberating for a week over something you could change in an hour
is waste. The few decisions that are expensive to reverse — data models, service
boundaries, public contracts, choices of vendor — get the opposite treatment:
they are made slowly, with alternatives written down, with the people who will
live with the consequences in the room. The skill is telling the two apart, and
the default assumption should be "this is reversible" until you have shown it is
not.

## 3. Make the change small

Small changes are easier to review, safer to deploy, faster to roll back, and
simpler to reason about when something breaks. Almost every practice in this
handbook is downstream of this: short-lived branches, diffs under three hundred
lines, one logical change per commit, trunk-based development, progressive
delivery. A large change is not a large change; it is a sequence of small ones
that has not been sliced yet.

## 4. Automate the thing you would otherwise police

If a rule matters, a machine should enforce it: formatting, linting, the secret
scanner, the test gates, the branch protections. Human attention is scarce and
inconsistent, and asking people to remember a rule is asking them to fail it
eventually. Reserve human review for judgement — is this the right design, is
this well tested, will this be clear later — and let the pipeline handle
everything with a definite answer.

## 5. You build it, you run it

The team that writes a service operates it: they are on call for it, they get
paged by its alerts, they write its runbooks, they own its cost and its SLOs.
This is not a punishment; it is the feedback loop that makes the service good.
An engineer who will be paged at 3am for a missing timeout will add the timeout.
There is no separate operations team to hand problems to, and there is no "throw
it over the wall".

## 6. Blameless, always

When something breaks, the question is "why did the system allow this" and never
"who did this". People make mistakes constantly; a system that turns a normal
human mistake into an outage has a design problem, and that problem is the thing
worth fixing. "Human error" is not a root cause — it is the beginning of the
investigation. This is a stated value because it does not survive on its own:
the instinct to find someone to blame is strong, and it has to be actively and
repeatedly overridden, especially by whoever is most senior in the room.

## 7. Reliability is a feature, and it has a budget

Perfect reliability is infinitely expensive and nobody needs it. We state,
per service, how reliable it needs to be — an SLO — and we treat the gap between
that and perfect as a budget to spend on shipping features. When the budget is
healthy, ship. When it is spent, stop shipping features and fix reliability until
it recovers. This makes the trade-off explicit and shared, instead of an
argument that reliability loses every quarter.

## 8. The simplest thing that could possibly work

Reach for the boring, well-understood option first: one service before many, a
library before a platform, a cron job before a workflow engine, a column before
a new table. Complexity should be *pulled* in by a concrete, present need, never
*pushed* in because it might be needed later or because it is more interesting.
"We might need to scale this to a million users" is not a present need; when you
have ten thousand and the design is straining, that is a present need, and the
simple version you built will have taught you what the complex one actually
requires.

## 9. Leave it better than you found it, in small steps

When you are in a piece of code for another reason and you see something wrong —
a bad name, a missing test, a stale comment, dead code — fix it, in a separate
commit, in the same pull request. Not a rewrite, not a yak-shave: a small, safe
improvement. The codebase improves continuously through thousands of these, or
it decays continuously through their absence. What you do not do is let the
observation become a large "cleanup project" that never gets prioritised.

## 10. Disagree, decide, commit

Disagreement is welcome and is worked through in the open — in the pull request,
in the ADR, in the design review. Once a decision is made by the people
responsible for it, everyone commits to it fully, including the people who
argued against it. Re-litigating a settled decision in side channels is
corrosive. If genuinely new information appears, that is grounds to reopen the
decision explicitly — a new ADR, a new discussion — not grounds to quietly work
around it.

## When principles conflict

The ordering resolves most conflicts, but it helps to see how.

*Reader clarity (1) versus small changes (3):* a large clarity-improving refactor
tempts you to do it all at once. Principle 3 wins the tie — do the refactor as a
sequence of small, safe steps, each independently reviewable, even though the
intermediate states are slightly awkward. The reader is served in the end and
nobody has to review a thousand-line diff.

*Reversible decisions (2) versus simplest thing (8):* these usually agree — the
simple option is often the reversible one. When they diverge, it is because the
simple option paints you into a corner. Principle 2 wins: pay a little more
complexity now to keep the decision reversible, rather than take the simplest
path into something you cannot undo.

*You build it you run it (5) versus reliability has a budget (7):* a team drowning
in its own pages will want to declare everything a reliability emergency and stop
all feature work. The budget (7) is what keeps that honest — if the SLO is being
met, the pages are a tuning problem, not a stop-the-world problem, and the fix is
better alerting, not a moratorium.

*Blameless (6) versus disagree-and-commit (10):* after a decision that later
causes an incident, there is a pull toward "we told you so". Principle 6 forbids
it. The person who argued against the decision does not get to relitigate it in
the postmortem; the postmortem asks what the system should have done
differently, and a settled decision that turned out wrong is reopened through a
new decision, not through blame.

## These are not aspirational

Every principle here is one we have violated, seen the cost of, and recommitted
to. They are in this document because they do not hold automatically — the pull
toward the large change, the blamed individual, the clever unreadable line, the
speculative complexity, is constant. Naming the principle is how we notice we are
drifting and pull back. A principle nobody ever has to invoke is either obvious
or dead.
