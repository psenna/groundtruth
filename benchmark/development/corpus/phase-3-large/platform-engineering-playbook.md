# Platform engineering playbook

This is the operational backbone: the environments our code runs in, how they
are defined, how secrets and configuration reach a running process, how we plan
for load and cost, and what we do when a region goes away. It is written for an
engineer who is about to change one of these things and needs to know the rules
before they do.

## Environments

We run exactly three long-lived environments and resist the urge to add more.

**Production** is the real thing. It is the only environment with real user data,
real traffic, and real money attached. Every rule in this document is ultimately
about protecting it.

**Staging** is production's rehearsal space. It runs the same artefacts, the
same infrastructure definitions, and the same configuration *shape* as
production, with production-like but synthetic data. The continuous delivery
pipeline deploys every change here and runs smoke tests against it before
production. Staging is not a playground — if staging is broken, deploys are
blocked, so it is treated with the same seriousness as production.

**Development** is per-engineer and ephemeral. It is spun up on demand, it is
allowed to be messy, and nothing depends on it. It exists so that an engineer
can run the system end to end without touching staging.

We do not have a "QA environment", a "demo environment", or a "pre-prod
environment". Each of those is a request to duplicate the maintenance burden and
a place for configuration to drift. A genuine need for an isolated environment —
a load test, a security assessment — is met by standing up a temporary
production-shaped environment from the same definitions and tearing it down
after.

## Infrastructure as code

Every piece of infrastructure — compute, networking, databases, DNS, IAM roles,
alerting — is defined in code in a repository, reviewed like application code,
and applied by automation. There is no console-clicking to create production
resources, and access to do so is restricted specifically so that the code stays
the source of truth.

The definitions are parameterised by environment, not copied per environment. A
change to how a service is deployed is one change to one module that flows to all
three environments through the normal promotion path — staging first, production
after it has been observed. When someone makes a change directly to a running
environment during an incident, the follow-up is to bring the code back in sync,
and that follow-up is a tracked action item, not an optional tidy-up.

State — the automation's record of what exists — is stored centrally, locked
during changes so two people cannot apply at once, and backed up. A corrupted or
lost state file is a serious incident because it disconnects the code from
reality.

## Configuration and secrets

Configuration and secrets are different things and are handled differently.

**Configuration** is non-sensitive settings that vary by environment: log level,
feature-flag defaults, the URL of a dependency, timeout values. It lives in the
infrastructure repository, it is reviewed, and it is delivered to the process as
environment variables at deploy time. A configuration change goes through the
pipeline like a code change because it can break production just as effectively.

**Secrets** are values that grant access: database passwords, API keys, signing
keys, tokens. The rules are absolute:

- A secret never appears in a source repository, a container image, a log line,
  a commit message, an error message, an environment definition file, or a chat
  message. Not encrypted, not base64'd, not "just for testing". Never.
- Secrets live in a dedicated secret manager. The infrastructure code references
  a secret *by name*; the value is injected into the process at startup by the
  platform and is never written to disk.
- Every secret has an owner and a rotation schedule. A secret that cannot be
  rotated without downtime is a design flaw to be fixed, not a fact to live with.
- Access to a secret is scoped to the workloads that need it and is audited. An
  engineer generally cannot read a production secret directly; they can see that
  it exists and when it was last rotated.
- A secret that has been exposed — pasted somewhere it should not be, committed
  and force-pushed away, printed in a log — is considered compromised and is
  rotated immediately, before the cleanup of wherever it leaked. The order
  matters: rotate first, tidy second.

The secret scanner in the pipeline rejects any change that looks like it
contains a credential, and that rejection is never overridden. If it fires on a
false positive — a test fixture that looks like a key — the fix is to make the
fixture obviously fake, not to disable the scanner.

## Capacity planning

We plan capacity from observed demand plus a stated headroom, not from
guesswork. For each service we track the resource that will run out first —
usually CPU, sometimes memory, sometimes a connection pool or a downstream quota
— and we keep enough headroom above the trailing peak to absorb a normal spike
and a normal traffic-shifting event without paging anyone.

Autoscaling handles the daily and weekly cycle. Capacity planning handles the
things autoscaling cannot: a launch, a seasonal peak, onboarding a large
customer, or the slow organic growth that will exhaust a fixed resource in three
months. The output of a capacity review is either "we are fine until date X" or
a dated action to add capacity, and someone owns that action.

Load tests are run against a temporary production-shaped environment before any
event we expect to move the numbers. A load test against staging is not valid —
staging is not sized like production and the result does not transfer.

## Cost

Cost is an engineering metric, tracked and owned like latency. Every service's
cost is attributed to it through tagging, and the owning team sees its monthly
spend and its cost per unit of work (per thousand requests, per gigabyte
processed). A sudden cost increase is treated like a latency regression: someone
investigates.

We do not chase cost at the expense of reliability or engineering time — an
engineer-week spent shaving a rounding error off the bill is a bad trade. But a
service whose cost per request is drifting up while its traffic is flat has a
leak, and leaks compound.

## Disaster recovery

A disaster is the loss of a whole region or a whole data store, not a single
failed host — that is just Tuesday and the platform handles it automatically.

For each critical data store we state two numbers: how much data we can afford to
lose (the recovery point) and how long we can afford to be down (the recovery
time). These numbers are chosen with the business, not by engineering alone, and
they drive the design: a recovery point of five minutes means continuous
replication, a recovery point of a day means nightly backups are enough and are
cheaper.

Backups are only real if they are restored. Every critical store has an
automated restore drill on a schedule — the backup is restored into a temporary
environment and checked — and a restore that has not been exercised in the last
quarter is assumed to be broken.

The failover procedure for each critical system is written as a runbook,
rehearsed, and short enough to execute under stress. A failover plan that
depends on a specific person being awake is not a plan.

## Access and change control

Access to production is granted by role, not by person, and every role is the
minimum that job needs. An engineer on a team can deploy that team's services and
read their logs and metrics; they cannot read production secrets, cannot alter
infrastructure outside their services, and cannot touch another team's systems.
Broad access — the ability to change any infrastructure, to read any secret — is
held by a small on-call platform rotation and is time-boxed and audited when
used.

Every change to production, whether through the pipeline or through a break-glass
manual action, leaves a record: what changed, who initiated it, when, and why.
The pipeline records this automatically. A manual action during an incident is
recorded by the scribe in the incident log and reconciled into the
infrastructure code afterward. An environment that has drifted from its code —
where the running reality and the committed definition disagree — is treated as a
low-grade incident until it is reconciled, because every drift is a place where
the next automated change will do something surprising.

## Platform changes

The platform itself — the deployment system, the secret manager, the
observability stack, the CI runners — is software, and it is changed the same way
application software is: small changes, reviewed, rolled out progressively
staging-first, reversible. A platform change that would affect every team is
announced, rolled out to one willing team first, observed for a week, then
widened. The platform team does not get to skip its own rules because it is
"infrastructure"; a bad platform change has a blast radius of the entire
engineering organisation, so if anything the bar is higher.

## Onboarding a new service

A new service does not get bespoke infrastructure. It is created from the
standard service module, which gives it — from the first commit — a pipeline, the
three environments, the standard observability wiring, an SLO template, secret
management, and cost tagging. The team fills in the service-specific parts. This
is deliberate: a service that starts with the standard platform integration
stays maintainable, and a service that starts with something hand-rolled becomes
the thing nobody wants to touch. If the standard module genuinely does not fit,
that is feedback for the module, raised as an issue, not a licence to go around
it.

## What this playbook does not cover

The day-to-day observability of running services — metrics, logs, traces, SLOs,
alerting — is a separate concern with its own strategy document. This playbook is
about the platform those services run on; that one is about watching the
services themselves.
