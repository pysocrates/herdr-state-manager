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

## Task decomposition

Tasks already form a project-local forest through parent_id. No schema migration or
reuse of project identifiers is needed. hierarchy.py builds a deterministic iterative
preorder and computes direct-child and all-descendant state summaries. Services
augment nodes with confirmed owner profiles and registered pane UUIDs; API, CLI, TUI,
MCP and recovery use the same data. No client duplicates progress rules.

Complete tree reads include terminal descendants. Recovery is smaller: active tasks
plus their ancestor chains, with complete rollup counts across closed branches.
Terminal parents are retained and warned about when they have active descendants.
Counts are observational; parent lifecycle/version and worker ownership remain explicit.
Creating a child does not mutate the parent record. Reparenting changes the child's
version, is checked against cycles/project scope, and audits old/new parent IDs.
Governed reparenting and paused-tree escape paths are rejected rather than transferring
scope authority implicitly.

## Governance extensions

HSM remains a persistent state authority, not an execution engine. Existing shared
services now consult `governance.py` for contract/delegation policy, `durable.py` for
revision-bound outputs and exception/remediation bookkeeping, and `interventions.py`
for operator pause and scope. `reporting.py` provides read-only recovery and metrics;
these modules reuse the same authorization, transaction and task forest mechanisms.

Migration 0004 adds immutable contract revisions and delegation provenance, 0005
adds deliverables/revisions/evidence/checkpoints and extends existing incidents with
exceptions and remediation associations, and 0006 adds local governance pause fields.
Historical events record applicable contract IDs at write time. New fields remain
compatible with uncontracted legacy tasks and missing optional JSON limits.

Contract amendments require explicit operator authority and expected task/contract
versions. SQLite serialized writer transactions protect budget checks and writes.
Policy denial uses a savepoint: roll back domain mutations, then persist only a
sanitized denial audit and idempotent receipt. Authorization failures do not leak
project-specific denial information. Replayed requests never consume another budget
slot. No failure or exception grants scope expansion automatically.

Deliverables store immutable artifact revision references; every changed revision
requires fresh validation. Supersession is explicit and same-task. Checkpoint evidence
and constraints remain available after task/session failure. A recorded next action
is not an evergreen grant: recovery rechecks eligibility and actual mutations always
revalidate authority. Canonical failure identity and existing exact-action approvals
protect retry accounting without introducing a second approval subsystem.

Pause is independent of lifecycle and inherited through all ancestors, including
uncontracted tasks. It preserves ownership/artifacts and gates new mediated work;
explicit safe closure/bookkeeping exemptions are documented in the API contract.
Neither pause nor resume controls runtime processes. Optional adapters stay
observational; unsupported process control is reported rather than simulated.

Task-specific recovery includes terminal tasks. `/state` keeps its v1 shape and
active-with-ancestors selection, adding an independently versioned governance section.
Governance views share a captured time and SQLite read snapshot with the event cursor.
Metrics read full relevant persisted history, not recent-event pages. Historical
root-at-creation is reconstructed only from recorded parent history, never today's
mutable tree. Missing provenance, repeated-exception occurrence telemetry and context
utilization remain unavailable. Deterministic alerts do not infer model intent.

See [API](API.md) for exact schemas/semantics and [verification](VERIFICATION.md)
for tested boundaries. No hosted infrastructure or model dependency is introduced.
