# API v1

## Checkpoint B: contracts (schema revision 0004)

`GET /tasks/{uuid}/contract` requires project `read` and returns
`{data:{current: CONTRACT_OR_NULL, history:[CONTRACT,...]}}` in revision order.
History is complete, unpaginated and read-only, including on terminal tasks.

`POST /tasks/{uuid}/contract` requires operator role, project `tasks:write` and
`Idempotency-Key`. Both initial creation and amendment use the same route:

```json
{
  "expected_version": 1,
  "expected_contract_version": 0,
  "reason": "Approved decomposition and resource scope",
  "contract": {
    "objective": "Implement the parser",
    "resource_labels": ["repo:parser"],
    "repository_path": null,
    "worktree_path": null,
    "base_commit": null,
    "max_children": 2,
    "max_descendant_depth": 1,
    "runtime_seconds": 3600,
    "retry_budget": 1,
    "required_deliverables": ["parser-tests"],
    "prohibited_operations": ["task.transition.running"],
    "stop_when_satisfied": true,
    "expansion_requires_authorization": true
  }
}
```

201 returns `{data:{contract:CONTRACT,task_version:2}}`. Contract records contain
id, task_id, revision, full specification, actor, reason, created_at,
authorization_started_at, expected_task_version and expected_contract_version.
Amendments send the current task version and current contract revision. They are
full replacements, not patches: omitted limits default null, deliverables default
[], paths/base_commit default null. Objective is required. Omitted allowed labels
default null (unrestricted); [] allows no declared resources. Non-null labels also
replace the task's declared labels; null leaves its labels unchanged. Never submit
secrets in reasons or any persisted metadata.

Task POST additionally accepts `resource_labels` (default []) and `spawn_reason`.
For a child under any contract, a nonblank spawn_reason is required; parent_id uses
the existing task creation route. `authorized_by` is never accepted from callers.
Task responses include immutable `delegation_history` (original parent, governing
revision IDs, authorizer, reason and time) and absolute `delegation_depth`.

Under governance, ordinary PATCH cannot change objective/paths/base_commit/labels.
Governed parent changes, including detach and moving an uncontracted subtree with
a contracted descendant, are denied rather than implicitly transferring authority.
Contract-free reparenting still works. The task lifecycle matrix is unchanged.
Every ancestor's direct-child lifetime budget, relative descendant depth, exact
resource labels and elapsed authorization window apply to child creation. Expiry
also gates ordinary PATCH and transitions, except valid failed/cancelled exits.
The window starts at the persisted first contract revision, not process start;
amendments and restarts do not reset it. Resource labels match by exact equality,
including non-null path strings; no glob/prefix/path access is inferred.

Governance denial returns 409 with a stable code: `child_budget`,
`descendant_depth`, `resource_scope`, `spawn_reason_required`,
`authorization_expired`, `contract_amendment_required`, `prohibited_operation`, or `governed_reparent`.
It commits only sanitized audit evidence and, for idempotent POST, the cached
denial response. No partial task/revision/delegation mutation survives. Retrying
the same POST key/payload returns that denial for 24h, even after policy changes;
a deliberate new attempt needs a new key. Other validation/auth/stale failures
retain rollback behavior. PATCH still has no idempotency key protocol.

`prohibited_operations` defaults to [] and accepts at most 100 nonempty strings,
each at most 200 characters. Exact case-sensitive enforced identifiers are
`task.create` (child creation under the contract), `task.patch` (ordinary PATCH),
`task.transition` (all transitions), and `task.transition.<state>` for each lifecycle
state in the transition schema. All applicable ancestor prohibitions apply; valid
failed/cancelled exits always remain available. Unknown labels are advisory only;
there are no wildcards or arbitrary tool/command restrictions. These identifiers
do not prohibit explicit operator contract amendments or other HSM endpoints.

`stop_when_satisfied` and `expansion_requires_authorization` are strict booleans,
both default true. Stop-on-satisfaction is persisted now; enforcement with
deliverables belongs to C. There is no automatic scope expansion even when
expansion_requires_authorization is false: every amendment remains an explicit
operator action. Older stored contract JSON may omit these fields; effective
defaults are [], true, true respectively, without rewriting historical JSON.

Every newly written task-associated event has `details.governing_contract_ids`,
captured at write time in task-to-root order (including every contracted ancestor;
[] for no contract). Denials capture authoritative ancestry after rollback in the
same writer transaction. `contract.revised` also identifies the new `contract_id`
and revision. Later amendments never relabel old events. Preexisting events
without this field have unknown attribution, not inferred current attribution.

**B limitation:** retry budgets, stop-on-satisfaction and deliverable requirements are stored but not
operationally enforced until checkpoint C. B does not infer work semantics,
observe external retries, validate external files, execute arbitrary tools or kill
processes. See DESIGN_CONTRACT for exact numeric limits, legacy migration baseline,
authorization-window and conservative reparent semantics.

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
reserve a key except the explicit governance denials documented above.
Register session/pane retries are not idempotent; inspect registrations
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

## Task hierarchy and multi-agent decomposition

Use project_id to isolate a project, parent_id to link a work item to its larger task,
and owner_session_id to assign that individual item to an agent. Nested subtasks are
supported; roots have parent_id=null. Creation and expected-version PATCH already
accept parent_id. Setting parent_id=null detaches an active task; changing it records
previous/new parent UUIDs in its task.updated event. Ownership changes similarly record
previous/new session UUIDs. Neither changes descendant ownership or lifecycle.

New read endpoints (read action and project grants required):

| Route | Result |
|---|---|
| GET /tasks/tree[?project=slug] | All authorized tasks as a complete forest |
| GET /tasks/{uuid}/tree[?project=slug] | That task and all descendants |
| GET /tasks?parent_id=UUID | Paginated immediate children |
| GET /tasks?parent_id=none | Paginated root tasks |

Tree endpoints accept only optional project, have no pagination/cap, and include all
states. The subtree root is displayed at depth=0; root_id remains the original project
hierarchy root. Cross-project filters and out-of-scope parent UUIDs cannot bypass grants.
List filters state/profile/date/parent_id may be combined; summaries still count all
children, independent of the list filter.

Tree data contains nodes, root_ids, selection (all/subtree) and
truncation.nodes=false. Nodes are flat preorder, sorted by project, then task key and
UUID among siblings. Each node contains all ordinary task fields plus:

- parent_key, depth, root_id, child_ids.
- owner_profile (a label, not a session identity), pane_ids (unterminated assignments).
- child_summary (direct children) and descendant_summary (all levels, excluding self).

Each summary contains total, completed, active, blocked, awaiting_approval, failed,
cancelled and by_state counts. completed excludes failed/cancelled; active includes
queued, planning, running, awaiting_approval, blocked and review. These are counts,
not effort estimates, and never update the parent's version/state. Child creation
changes a computed parent summary without constituting a parent write.

Task create/get/patch/transition/list responses include these computed fields. An
idempotent replay returns the original response, so GET if current rollups are needed.
Recovery adds task_hierarchy with selection=active_with_ancestors: it contains every
active task and its ancestor chain, including terminal ancestors. Closed branches are
intentionally omitted from that view, but summaries count them; fetch a subtree for
full history. The projection is uncapped; it does not silently truncate active work.
Existing active_tasks remains present and includes the same computed fields.

No transition cascades. Parent completion is still an explicit allowed lifecycle
transition. A terminal parent with active descendants appears in snapshot warnings.
This permits intentional follow-up beneath a terminal task while keeping unfinished
work visible. Parent linkage is grouping, not a dependency/scheduling engine.
