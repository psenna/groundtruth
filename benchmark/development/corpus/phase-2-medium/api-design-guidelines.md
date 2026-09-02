# API design guidelines

These rules apply to the HTTP APIs our services expose to each other and to
clients. They exist so that a consumer who has integrated with one of our APIs
can guess how the next one behaves.

## Resources and methods

Model the domain as resources with stable identifiers. Use the HTTP methods for
what they mean: `GET` is safe and idempotent, `PUT` and `DELETE` are idempotent,
`POST` is neither. A `GET` that changes state is a bug, not a shortcut.

Collection endpoints are plural nouns. A sub-resource is a path segment, not a
query parameter. Actions that genuinely do not fit a resource verb — "retry this
job", "resend this invite" — are modelled as a `POST` to a sub-path named for the
action, and this is a last resort, not a default.

## Versioning

The version is a major-version prefix in the path. A breaking change means a new
major version and a documented migration window during which both are served.
Adding an optional field, adding an endpoint, or adding an enum value a client is
told to tolerate are not breaking changes and do not bump the version.

Clients are required to ignore fields they do not recognise. A client that
breaks when we add a field is a client that was built wrong, and we will still
try not to surprise it, but the contract is "tolerate unknown fields".

## Errors

Every error response has the same shape: an HTTP status that matches the class of
problem, a stable machine-readable `code` string, and a human-readable `message`
that is safe to log. The `message` is for an engineer reading a log, never for an
end user and never containing anything sensitive. Validation errors list every
problem at once, not the first one found.

The status codes we use and what they mean: `400` malformed request, `401`
missing or bad credentials, `403` authenticated but not allowed, `404` no such
resource, `409` the request conflicts with current state, `422` well-formed but
semantically invalid, `429` rate limited, `503` the service is up but a
dependency is not.

## Pagination

List endpoints are paginated from the first release, even when today's data set
is small. Pagination is cursor-based: the response carries an opaque `next`
cursor, and the client passes it back. Offset-and-limit pagination is not used
because it behaves badly when the underlying data changes between pages.

## Idempotency for writes

A client that needs to safely retry a `POST` sends an idempotency key header. The
service records the key and the response for a bounded window and replays the
stored response for a repeat. This is required for any endpoint that moves money
or sends a message.

## Timeouts and retries

Every call between our services has a timeout set by the caller — there is no
"wait forever". A caller retries only idempotent operations, only on a timeout or
a `503`, with jittered backoff and a small cap. Retrying a `400` or a `409` just
wastes both services' capacity.
