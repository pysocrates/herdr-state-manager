# Architecture

The [design contract](DESIGN_CONTRACT.md) defines relationships, the complete state
transition matrix, authorization matrix, concurrency, retry and recovery semantics.

REST is authoritative. Flask validates inputs with Pydantic, resolves bearer identity,
opens a transaction and invokes shared services. The CLI, Textual display and optional
MCP facade call authenticated REST, so there is no parallel authorization or domain
implementation. Response envelopes and snapshot shape are Pydantic-validated.

SQLAlchemy models store state in SQLite. Alembic migrations are frozen schema changes;
startup never creates or migrates tables. Each connection enables foreign keys, WAL
and a 5-second busy timeout. Writers begin IMMEDIATE before checking versions, so
competing writers serialize. State, event, and idempotency response share one commit.
Busy writes return 503 and can be retried using the same idempotency key.

Recovery starts an explicit read transaction before querying projects. The snapshot's
event cursor and rows therefore share one SQLite snapshot, even during concurrent
writes. Revision is the latest visible event cursor, not an ETag for polling timestamp
changes. Active states are queued/planning/running/awaiting_approval/blocked/review.
All active tasks are included, plus unended registrations, blocked tasks, outstanding
approvals (including approved unused grants), incidents and adapter health.
Only recent events are capped; truncation is explicit.

Panes and agent sessions are independent entities with optional relationships.
Repository paths are stored as data; HSM never traverses or inspects them.

Reconciliation takes a nonblocking flock beside the local database for the entire
run, reads registrations, performs bounded external observations outside write locks,
then rechecks registrations and persists results in a short write transaction.
Missing/available/unavailable observations never transition tasks. Incident identity
is (project, type, resource); a continuing discrepancy updates last_seen only, clearing
it emits one resolution, and recurrence reopens the same incident with another event.
The first_seen timestamp describes the current occurrence; events retain prior ones.
Unavailable sources never prove missing resources or resolve existing missing incidents.
Stale registered heartbeats (>15 minutes) raise observational incidents only.

Approvals are operation-specific grants, separate from task states. Resolution and
consumption are serialized, versioned and audited. No terminal task can request or
consume a grant. Expiry is enforced on every resolve/consume, regardless of timer
availability. Reads expose effective_status; reconciliation materializes expired
status and emits expiry events. Consumption is deliberately not repeatable; after a
lost response inspect consumed_by/consumed_at, rather than executing twice.

Trust boundary: database/configuration filesystem access is local administration.
Agent and operator tokens are distinct, and agent grants enumerate project/action
access. No transcripts or secrets belong in metadata. Application logging excludes
request payloads and credentials. Remote access requires TLS or SSH tunneling and a
rate-limiting authenticated reverse proxy. No in-process distributed coordination.
