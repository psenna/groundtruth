# Observability strategy

Observability is our ability to answer new questions about the running system
without shipping new code to ask them. We invest in it deliberately because the
alternative — adding a log line and waiting for the problem to recur — is too
slow during an incident.

## The three signals

**Metrics** are cheap, aggregate, and always on. Every service exports request
rate, error rate, and latency distribution for every endpoint, plus a small
number of domain metrics that matter to that service. Metrics answer "is
something wrong and roughly where".

**Logs** are structured — key/value, not prose — and carry the request
identifier that ties them to a trace. Logs answer "what exactly happened for
this one request". They are sampled on the happy path and kept in full for
errors. We do not log anything sensitive; a log line is assumed to be readable
by any engineer.

**Traces** follow one request across service boundaries and show where the time
went. Every inter-service call is a span. Traces answer "why was this slow" and
"which dependency failed".

## SLOs, not vanity targets

Each user-facing service has a small number of service level objectives stated
as "X% of requests over the trailing 28 days succeed within Y milliseconds". The
gap between the SLO and 100% is the error budget. When the budget for a period is
spent, the team stops shipping features and spends its effort on reliability
until the budget recovers. This is the mechanism that makes reliability a
shared, funded priority rather than a thing that loses to the roadmap every
quarter.

We do not set SLOs at 99.99% by reflex. A tighter SLO costs real engineering
effort, and most of our services do not need one. The SLO is negotiated with the
people who depend on the service.

## Alerting

An alert pages a human, so an alert must be both urgent and actionable. We alert
on symptoms the user feels — the SLO is burning too fast, the error rate is
spiking — not on causes like "CPU is high" or "a host is down", which may or may
not matter. Every alert links to a runbook. An alert that fires and is
acknowledged without action is deleted or downgraded within the week; alert
fatigue is a reliability risk in its own right.

## Dashboards

Every service has one overview dashboard that a person unfamiliar with the
service can read during an incident: the SLO status, the three signals for the
top endpoints, and the health of its dependencies. Deep-dive dashboards are
built as needed and are not expected to be self-explanatory.

## What this does not cover

Capacity planning, cost attribution, and the mechanics of the platform that runs
all of this are a separate concern and are described in the platform playbook.
