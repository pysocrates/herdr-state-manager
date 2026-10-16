# API v1

Base `/api/v1`. Send `Authorization: Bearer TOKEN` for every endpoint except
`GET /health/live`. Content-Type application/json for writes. All identifiers are
UUIDs except project slugs. Task keys are unique inside a project.

Success: `{"data": ...}`. List success also includes
`"pagination":{"offset":0,"limit":50,"has_more":false}`. Project listing and
state snapshots are not paginated. List limit 1..200, offset >=0; stable ID ordering,
events descending cursor. All timestamps are UTC ISO-8601 ending Z.
Errors: `{"error":{"code":"stale_version","message":"Task version has changed","details":[]}}`.
400 malformed JSON; 401 missing/invalid credential; 403 project/action denied;
404 absent resource; 409 invalid transition/version/constraint/approval mismatch;
413 body over 64 KiB; 422 invalid fields/query; 503 SQLite unavailable/busy/schema.
Metadata configuration is bounded to 8 KiB; unknown input fields fail validation.
No rate limiter is embedded: configure remote proxy rates. Tokens never belong in URLs.

## Endpoints and grants

| Routes | Methods | Action |
|---|---|---|
| /projects | GET, POST | read / projects:write (operator) |
| /tasks | GET, POST | read / tasks:write |
| /tasks/{uuid} | GET, PATCH | read / tasks:write |
| /tasks/{uuid}/transition | POST | tasks:write |
| /tasks/{uuid}/events | GET | read |
| /agent-sessions | GET, POST | read / runtime:write |
| /agent-sessions/{uuid} | GET, PATCH | read / runtime:write |
| /agent-sessions/{uuid}/heartbeat | POST | runtime:write |
| /panes | GET, POST | read / runtime:write |
| /panes/{uuid} | GET, PATCH | read / runtime:write |
| /panes/{uuid}/heartbeat | POST | runtime:write |
| /approvals | GET, POST | read / approvals:request |
| /approvals/{uuid} | GET | read |
| /approvals/{uuid}/resolve | POST | approvals:resolve (operator) |
| /approvals/{uuid}/consume | POST | approvals:consume (original requester) |
| /events, /incidents | GET | read |
| /state, /state/projects/{slug} | GET | read |
| /health/live | GET | unauthenticated liveness |
| /health, /health/ready | GET | authenticated schema readiness |

All resource routes check their project's grant. Agent lists include only granted
projects; explicit unauthorized project filters return 403. Nested references must
belong to the same project. Credential identity, not supplied actor text, is audited.

## Creation and retries

Project POST: `{"id":"example","name":"Example","configuration":{}}` (201).
Session POST: `{"project_id":"example","profile":"schema","external_session_id":"actual-id"}` (201).
Task POST (201), requires `Idempotency-Key: unique-operation-key`:

```json
{"project_id":"example","task_key":"SCHEMA-1","title":"Schema work","objective":"Implement migration","owner_session_id":"SESSION_UUID","worktree_path":"/metadata/only"}
```

Optional task fields: parent_id, owner_session_id, repository_path, worktree_path,
base_commit. Return contains UUID, state queued, version 1, created/updated/completed
timestamps, and all metadata. Pane POST:
`{"project_id":"example","external_pane_id":"actual-pane-id","task_id":"TASK_UUID","agent_session_id":"SESSION_UUID"}`.
Provider defaults herdr, optional noop. Heartbeat POST body `{}`; it does not prove
runtime observation. Runtime PATCH accepts runtime_status; panes additionally accept
task_id/agent_session_id, nullable to clear assignments. Terminated status records
an end timestamp. These explicit endings are final registrations; re-register to resume.

Task PATCH: `{"expected_version":1,"title":"Updated title"}`.
Task transition POST: `{"expected_version":1,"state":"planning"}` plus Idempotency-Key.
PATCH cannot set state. Same-state, terminal edits, and forbidden edges return 409.
See complete matrix in DESIGN_CONTRACT. Every successful task edit increments version.

Keys scope to principal + POST route + key, 1..128 non-space printable ASCII. Same
canonical validated payload returns original status/body without new events for 24h;
a changed payload conflicts. Expired keys may be reused; inspect before retrying old
operations. Replays still check current project grants. Failed operations do not
reserve a key. Register session/pane retries are not idempotent; inspect registrations
after ambiguous timeouts (pane identity has a per-project unique constraint).

## Approvals

POST /approvals requires Idempotency-Key:

```json
{"project_id":"example","task_id":"TASK_UUID","operation":"deploy","target":"staging","action":"deploy release abc","artifact":"sha256:abc","expires_at":"2026-10-17T12:00:00Z"}
```

Expiry must be future and at most seven days. Use your current future timestamp.
Request identity is derived from authentication. Operator resolution:
`{"expected_version":1,"decision":"approved","note":"Reviewed immutable release"}`.
Decision can be approved/rejected. Resolution increments version and never transitions
the task. CLI requires confirmation; a REST operator caller supplies its own UI.

Consumption POST uses the original requester credential:
`{"expected_version":2,"task_id":"TASK_UUID","operation":"deploy","target":"staging","action":"deploy release abc","artifact":"sha256:abc"}`.
All fields must match exactly; only one concurrent consumption succeeds. Expired,
used, stale, rejected and mismatch requests conflict. Read consumed_at/consumed_by
after ambiguous results. No execution occurs inside HSM. Status remains approved
with consumption fields; effective_status reflects expiration for unused grants.

## Queries and recovery

Tasks: project, state, profile (owner profile), created_after, created_before (inclusive,
timezone required), limit, offset. Sessions: project, profile, limit, offset. Panes:
project, limit, offset. Events: project, task_id, limit, offset. Approvals/incidents:
project, status (persisted status), limit, offset. Project filter means exact slug.

State accepts project and recent_events (default 30, 1..200). Response data:
schema_version=1, project/null, generated_at, revision (event cursor), projects,
active_tasks, agent_sessions, active_panes, pending_approvals, outstanding_approvals,
blocked_tasks, unresolved_incidents, adapter_health, recent_events, warnings,
truncation. Outstanding approvals include pending/approved unused records, including
expired records until housekeeping. Pending approvals contain only unexpired pending
requests. All active resources are uncapped and ordered by project then UUID.
Recent events are descending cursor; truncation.recent_events discloses more history.

Recovery additionally includes related_ended_sessions (ended owners of active tasks)
and related_terminated_panes (terminated registrations assigned to active tasks).
These preserve ownership/assignment context even after an explicit runtime ending.
Approval responses also include task_key and task_state, so recovery exposes requests
that remain unresolved after their task becomes terminal. Such grants cannot be
consumed. Resolving or expiring them remains separate from task cancellation.
