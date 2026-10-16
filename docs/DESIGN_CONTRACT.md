# HSM v1 design contract

## Relationships and trust
Projects own tasks, agent sessions, panes, events, approvals and incidents.
Tasks have optional same-project parents and owners (agent sessions). Panes have
optional task and agent-session assignments; neither relationship is one-to-one.
Identifiers are UUIDs except project slugs. Task keys are unique within a project.
Parent cycles and cross-project references are rejected. Paths are opaque metadata.
Events have a monotonic integer cursor plus UUID; event actor is authenticated.
Direct database access and configuration access are local administrative powers.
No API deletion or event mutation. Retain events indefinitely in v1; archival or
administrative deletion requires an offline backup and documented maintenance.
Metadata is at most 8 KiB; requests at most 64 KiB. Never submit secrets/transcripts.

## Complete task transition matrix
Same-state transitions return 409. Omitted edges are forbidden.

| From | Allowed destinations |
|---|---|
| queued | planning, blocked, cancelled, failed |
| planning | running, awaiting_approval, blocked, failed, cancelled |
| running | review, awaiting_approval, blocked, failed, cancelled |
| awaiting_approval | planning, running, review, blocked, failed, cancelled |
| blocked | planning, running, review, awaiting_approval, failed, cancelled |
| review | running, awaiting_approval, blocked, completed, failed, cancelled |
| completed | none |
| failed | none |
| cancelled | none |

Approval decisions never cause transitions. A rejected approval leaves the task
unchanged; the orchestrator explicitly blocks, replans or cancels. Resumption is
explicit. Terminal tasks cannot be patched; follow-up work uses a new linked task.

## Authorization matrix
Credentials are configured as JSON records with token, identity, role, explicit
project list, and action list. Tokens must be >=32 characters and unique. Agents
cannot use wildcard projects or actions. Operators may use explicit wildcards.
All operations require authentication except liveness. Readiness requires auth.

| Operation | Required action | Additional condition |
|---|---|---|
| project registration | projects:write | operator |
| project listing, snapshots, resources, events | read | project grant |
| task creation/edit/transition | tasks:write | project grant |
| session/pane registration/edit/heartbeat | runtime:write | project grant |
| approval request | approvals:request | project grant |
| approval resolution | approvals:resolve | operator, project grant |
| approval consumption | approvals:consume | original requester, project grant |

Cross-project lists return only authorized projects. Out-of-scope resource access
returns 403. Disabled projects reject writes but remain readable for recovery.
CLI uses REST; local init/migrate/backup/reconcile use administrative filesystem trust.

## Transactions, concurrency and retries
Every write transaction begins IMMEDIATE, uses a bounded SQLite busy timeout,
and commits state and its audit event together. All task edits/transitions require
expected_version, incremented on success. Stale requests return 409 with no event.
Serialized approval resolution and consumption accept only eligible current state.
Creation and transitions require Idempotency-Key (1-128 printable ASCII characters).
Scope: authenticated identity + method/route + key. Canonical validated payload is
hashed; a matching retry returns original body/status, mismatched payload returns
409. Retain for 24 hours; expired keys can be reused, so clients must not retry
older operations without inspection. Replay checks still require current grants.

## Approval protocol (Milestone 3)
Requester submits exact operation, target, artifact revision, task and required
future expiry (maximum 7 days). Operator reviews and resolves pending once.
Requester consumes approved authorization by presenting exact matching action,
target, fingerprint and task. Atomic consume checks expiry, requester, scope and
unused status and records the exact grant. A lost consume response is inspected
through GET; retries do not issue another grant. Expired/rejected/mismatched/used
requests conflict. HSM cannot enforce commands executed by noncooperating tools.

## Recovery schema
GET /api/v1/state[?project=slug] or /state/projects/slug returns {data: snapshot}.
Snapshot: schema_version, project (null for aggregate), generated_at (UTC), revision
(max visible event cursor), projects, active_tasks (including versions/ownership/
Git metadata), agent_sessions, active_panes, pending_approvals, blocked_tasks,
unresolved_incidents, recent_events, warnings, truncation. Active states are queued,
planning,running,awaiting_approval,blocked,review. Include all registered unended
sessions and unterminated panes, even unassigned. Active tasks are never capped.
Recent events default 30, max 200; disclose cap/truncation. Stable sorting by project
and ID; events descending cursor. A single explicit read transaction guarantees
consistent rows/cursor. generated_at naturally changes between reads.

## API conventions
Success {data: ...}; list also {pagination:{offset,limit,has_more}}. Errors
{error:{code,message,details}}. UUID resource IDs; project slug [a-zA-Z0-9_-].
UTC timestamps use Z. Lists offset>=0, limit 1..200, stable ID ordering. Date filters
are inclusive UTC instants. Unknown inputs fail validation (422); malformed JSON
400; auth 401/403; missing 404; conflicts 409; oversized requests 413. No in-process
rate limiter: localhost default, bounded sizes/pages/timeouts; remote deployment
requires TLS/auth proxy with configured per-credential rate limits.

## Discovery, 2026-10-16
Local Herdr 0.9.1: help verifies `api schema --json`, `api snapshot`, `pane list`
and `pane get`. Exported schema is inspected separately before adapter code.
Local Hermes 0.19.0: sessions list exposes formatted history and no advertised JSON
flag. Dashboard exists; inspect installed route code before selecting a metadata
interface. No live conversations or terminal output are needed. Until verified,
no-op observations are unavailable, never missing. Runtime adapters are Milestone 2.

## Milestone 1 plan
1. Package/config/SQLite and Alembic initial migration.
2. Shared authenticated services: projects, tasks, sessions, panes, events.
3. Atomic versions/idempotency and complete transitions.
4. Consistent recovery endpoint and REST-backed Typer/Rich CLI.
5. Tests for recovery after restart, scopes, races, retries and relationships.
6. Install/operations/API documentation and clean Git repository; no deployment/push.

## Implementation clarifications
All four milestones are implemented. Recovery also contains outstanding_approvals
(pending/approved unused grants, with effective expiry), adapter_health, and related
ended owner sessions/terminated task panes; they prevent loss of still-relevant context.
Revision is an event cursor, not an ETag for observation timestamp updates. Hermes
integration uses explicitly configured read-only metadata DB access verified against
the installed 0.19.0 schema; Herdr snapshot schema is protocol 22, bundled with 0.9.1.
No live pane output or session conversations were inspected. Production adapter access
and Ubuntu service activation remain operator integration steps.

## Task hierarchy refinement
Project identifiers remain software-project boundaries. Parent_id groups delegated
work; owner_session_id and pane registrations identify each work item's workers.
The hierarchy API exposes complete task forests/subtrees, direct-child filters and
computed ownership/progress fields. Recovery adds active_with_ancestors task_hierarchy,
including terminal ancestors and closed-descendant counts. Derived progress does not
change parent versions or lifecycle. No automatic parent/child transition, cancellation
cascade or agent spawning is introduced. Existing parent_id needs no migration.
