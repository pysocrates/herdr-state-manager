# API v1

## Checkpoint D: governance intervention (schema revision 0006)

`GET /api/v1/tasks/{uuid}/scope` requires the task project's `read` grant and
returns `{data:{task,contracts,remaining_budget,deliverables,unresolved_exceptions,
runtime_control,governance_decision,generated_at}}`. `task` is the ordinary full
task view; contracts are serialized current applicable TaskContract records,
nearest first. Deliverables include durable revisions/evidence (also on terminal
tasks), and exceptions are unresolved task exceptions, not runtime incidents.
`runtime_control` is always `{supported:false,acknowledged:false}`. The read-only
decision identifies paused ancestors and terminal state; it is not a grant and
cooperating runtimes must recheck authoritative mutations.

`POST /api/v1/tasks/{uuid}/pause` and `/resume` require operator role, project
`tasks:write`, `Idempotency-Key`, and JSON `{expected_version:1,reason:"investigate"}`.
Version is a strict positive integer; reason is a nonblank string of 1..4096
characters; extra fields are rejected. Success is 200 `{data:full_task_view}`.
Each operation changes only local governance state, task version/update time and
audit metadata. Stale versions, the same local state, or terminal tasks give 409;
insufficient grants give 403. Same-key same-payload retries return the original
response without another event/version change; altered payload gives 409. Replay
is historical receipt, not a fresh indication of effective state. Identity and
time come from the authenticated principal and server. Latest audit fields are
`governance_changed_by`, `governance_changed_at`, `governance_reason`; immutable
`task.paused`/`task.resumed` events preserve all prior reasons and actor/timestamp.

All task list/detail/tree/state hierarchy nodes share a `governance` object:
`paused`, `effective_paused`, `remaining_children`, `ready_deliverables`,
`required_deliverables`, `unresolved_exceptions`. Local pause differs from effective
pause inherited through **all** ancestors, even without contracts. Resuming a
locally paused descendant does not override an ancestor pause. Required counts
are local exact distinct keys (zero without contract); ready counts are all local
non-superseded deliverables with current passing evidence, not just required keys.

Scope `remaining_budget.constraints` carries each applicable owner's task/contract
ID and relative distance: lifetime direct-child slots, descendant depth remaining
from the selected task, remaining elapsed authorization-window seconds, aggregate
retry allowance, per-canonical-failure allowance and consumed-identity remainders.
`max_retries_per_failure` is the allowance for an unseen identity; the accompanying
map reports previously consumed identities. Top-level `remaining_children` is
the selected task's local allowance only (ancestor direct-child slots cannot limit
grandchildren); depth/window/retry top-level values are the minimum non-null
applicable constraint. Exhausted values clamp to zero; unconstrained values are
null, never inferred from process telemetry. Missing contracts produce null scalar
budgets and an empty constraints list. These are allowance reports, not permission
to execute: pause, lifecycle, resources and other rules still apply.

Pause denies descendant child creation, ordinary task PATCH (including ownership,
objective and resource changes), work transitions, contract amendment, generic
grant consumption and remediation request/authorization. Reparenting into/out of
a paused tree, or moving an ancestor containing paused descendants, is denied.
Failure/cancel remain lawful exits. Review/completion may close existing work only
through existing lifecycle, contract prohibition/window and required-deliverable
checks; pause does not waive those checks. Durable artifact/evidence, exception and
checkpoint bookkeeping remains available under existing validation, including
next-action eligibility. No pause/resume changes owners, artifacts or lifecycle,
refunds budgets, extends windows, starts/stops a process or restarts work. Optional
runtime adapters remain observe-only; D introduces no execution/control adapter.

## Checkpoint C: durable work (schema revision 0005)

All routes have the `/api/v1` prefix. New POST routes return 201 with `{data:...}`,
require project `tasks:write`, `Idempotency-Key`, and strict positive integer
`expected_version`. Read grants are required to resolve referenced records. Versions
refer to the resource named in the path (Task for task-scoped creation). Appending
artifact evidence/revisions or superseding increments Deliverable.version; creating
independent records does not change Task.version. Incident inspection/resolution/
remediation association increments Incident.version. Authorization increments both
Remediation.version and Approval.version. GET does not mutate or resume anything.

| POST route | Required body in addition to expected_version |
|---|---|
| /tasks/{id}/deliverables | key (1..200), title, reference, artifact_identity (1..512) |
| /deliverables/{id}/revisions | reference, artifact_identity |
| /deliverables/{id}/evidence | revision_id, exact artifact_identity, outcome passed/failed, reference |
| /deliverables/{id}/supersede | superseded_by (same-task deliverable UUID) |
| /tasks/{id}/checkpoints | revision_ids (0..100 distinct UUIDs); optional blockers (0..100 strings), next_authorized_action |
| /tasks/{id}/exceptions | failure_kind execution/validation/dependency/scope, description; optional nullable evidence_reference, proposed_action, scope_impact |
| /exceptions/{id}/inspect | no additional fields |
| /exceptions/{id}/resolve | note |
| /exceptions/{id}/remediations | approval_id |
| /remediations/{id}/authorize | expected_approval_version |

Text fields are 1..4096 characters unless noted. Empty/unknown fields, booleans as
versions, oversized lists and unrecognized actions fail strict schema validation.
There are no deletion, historical edit or restore/execute endpoints.

GET collections `/deliverables`, `/deliverable-revisions`, `/validation-evidence`,
`/checkpoints`, `/exceptions`, `/remediations` accept project, task_id, limit (1..200),
offset. Exceptions also accept persisted status; only task-exception Incident rows
appear here. `/incidents` continues to include reconciliation incidents and now
accepts task_id too. Each new collection supports `/UUID` detail independently of
active-task recovery. Deliverable detail includes immutable revisions and evidence
and current `validated`. Checkpoints include exact revision/evidence IDs,
contract_ids (task-to-root), completed_requirements, blockers, task_version, actor,
time and `restoration: read_only_no_resume`. Exception detail includes remediation
history and `remediation_grant`, the exact proposed Approval fields, not an approval.
Detail histories are complete; collection pages bound parent rows, not nested history.

Example artifact registration:
```json
{"expected_version":2,"key":"parser-tests","title":"Parser tests",
 "reference":"git:repo/path@commit","artifact_identity":"sha256:declared-digest"}
```
Pass the returned revision UUID and **exact same identity** when recording evidence.
A fresh revision never inherits validation, including when identity repeats. Latest
recorded evidence determines current revision validity; failed evidence invalidates
current readiness without deleting earlier checkpoint evidence. Artifacts/revisions/
evidence/checkpoints and exception recording remain available after task failure.
Required keys match only current non-superseded local task deliverables. Supersession
is one-time, same-task and acyclic, not a requirement-key alias.

Checkpoint `next_authorized_action` is null or `review`, `complete`, `record_evidence`.
Review/complete are checked against the current lifecycle and contract; no action is
executed. `record_evidence` requires an existing same-task deliverable revision and
the write/read grants needed to record evidence; it remains available on terminal
tasks because evidence is bookkeeping. No target yields `evidence_target_missing`.
This declaration does not bind a future evidence payload or waive its version/identity
checks. Completed requirements are server-derived from the supplied current
known-good references intersected with the active local contract. Historical
known-good revisions can be captured too, but do not assert current completion.
Later evidence/contracts cannot rewrite an existing checkpoint or make it new authority.

For remediation, request an ordinary Approval using exception.remediation_grant plus
project_id and expires_at. Its fields bind operation=`remediation.retry`, target and
task_id=task UUID, action=`retry:<failure_kind>`, artifact=SHA256 of canonical JSON
current contract IDs. Only an explicit operator decision creates authority. The
original requester associates the approval, then authorizes using both current
versions. These routes additionally require `approvals:consume`; current grants are
checked before idempotent replay. Approved, unexpired, unused exact-action grants
are consumed **atomically** with the retry ledger authorization. Any later failure
rolls back consumption. Generic `/approvals/{id}/consume` refuses remediation grants.
It also enforces applicable `approval.consume`, exact operation prohibitions,
authorization windows and stop-on-satisfaction; it never mutates task scope.

Canonical exception identity is task UUID + fixed failure_kind, not description,
incident UUID, approval UUID or idempotency key. Canonical retry identity additionally
includes fixed operation remediation.retry. Rejected matching approvals (including
unassociated rejected grants) permanently block equivalent retries with new IDs.
One exception per task/kind is deliberately conservative; resolution does not reset
rejections/budgets. `retry_budget` is the aggregate lifetime authorization cap over
each contracted task's recorded ancestry, shared across failure kinds and descendants.
`max_retries_per_failure` independently caps the lifetime authorized count for the
stable task/failure/action identity at every applicable local/ancestor contract.
Both are strict integers 0..1000000 or null (unlimited); zero forbids retries.
Legacy JSON missing the new field defaults effectively to null without rewriting
history. Both checks and approval consumption share one serialized transaction.
New approval IDs, contract revisions, resolution and terminal descendants do not
refill usage. Original entire ancestry counts earlier usage even when a contract
is first added later; tightening can grandfather existing use but permits no new
authorization until below both current caps.
Scope exceptions cannot authorize retries: seek an explicit amendment or separately
authorized task. Terminal tasks cannot consume any grant or authorize remediation.

Exception `evidence_reference`, `proposed_action`, and `scope_impact` are optional
nullable strict strings (1..4096 characters when non-null). They are persisted on
Incident, returned in exception reads, and copied to the `exception.recorded` audit
event. They are untrusted descriptive metadata, never fetched, executed or interpreted
as authorization/scope overrides. Do not include secrets. Missing values remain null
on legacy incidents. Project authorization precedes creation/read/audit disclosure.

Completion requires the active local contract's exact required_deliverables set.
Nonempty satisfied sets with stop_when_satisfied=true gate child creation, ordinary
PATCH, new-work transitions, approval consumption and remediation throughout ancestry.
Empty sets never trigger stopping. Review/completion remain permitted by this stop
gate, subject to other existing constraints; valid failure/cancellation, evidence,
checkpoint and exception bookkeeping remain available. There is no lifecycle cascade.
New denial codes include required_deliverables_missing, stop_when_satisfied,
retry_budget, max_retries_per_failure, remediation_rejected and contract_changed. Policy denials preserve
sanitized audit/replay only; ordinary validation and version conflicts roll back.

## Checkpoint B: contracts (schema revision 0004)

The following describes B's original boundary; C above supersedes its deferred
retry/deliverable/stop gates and extends approval-consumption enforcement.

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

## Checkpoint E: versioned recovery and governance metrics

All paths below are beneath `/api/v1`, return the normal `{data: ...}` envelope,
and require bearer authentication plus project `read` permission. No GET writes,
consumes grants, resumes tasks, opens artifact references or calls runtime adapters.

| GET route | Selection / query |
|---|---|
| `/tasks/UUID/recovery` | Exact task in **any** lifecycle state; no query parameters |
| `/tasks/UUID/metrics` | Exact task (not its subtree); no query parameters |
| `/governance/metrics` | All persisted tasks in permitted projects; optional `project=SLUG` |

Unknown/repeated parameters fail 422; out-of-scope project/task reads fail 403.
The companion agent helper permits all three routes and `/tasks/UUID/scope`.

## Recovery payload v1

`schema_version: 1`, `as_of`, and `event_cursor` accompany the existing scope fields:
`task`, `contracts` (applicable immutable revisions, nearest first),
`remaining_budget`, `deliverables` (full revisions/evidence), `unresolved_exceptions`,
`runtime_control`, `governance_decision`, and `generated_at` (equal to `as_of`).
Additional fields are `objective`, `local_contract` (null when absent), `checkpoints`
(ascending creation time/ID), `latest_checkpoint`, `ownership`, `readiness`,
`last_recorded_authorized_action`, `next_authorized_action`, `next_action_reason`,
`restoration: read_only_no_resume`, and source-labelled `runtime`.

Ownership preserves the registered owner ID and its session, even when ended.
Readiness separates `task_completed`, `has_requirements`, `required_keys`,
`validated_keys`, `missing_required_keys`, and `requirements_satisfied`. An empty
required set is satisfied but is not counted as a task with requirements satisfied.
Budget constraints expose owner/contract ID, revision, ancestor-relative distance,
limits, used lifetime delegation/retry authorizations and elapsed authorization
seconds, plus remaining allowances. Null limits remain unlimited/unspecified.
These seconds are an authorization window, **not** execution time.

The last recorded authorization is the latest persisted successful task create/
update/transition/pause/resume, contract revision, deliverable revise/validate/
supersede, remediation authorization or approval consumption event; null if none.
It is historical evidence, not a live grant. Other observation/request/checkpoint
events are not treated as execution authorization. Old event details are unchanged.

Only the latest checkpoint's explicitly recorded action can be projected. It is
suppressed with a reason when task version/contracts or its exact validation
evidence changed, current caller lacks write eligibility, or transition/window/
prohibition/required-output rules no longer permit the action. Recovery conservatively
suppresses **all** next actions for paused or terminal tasks, even though safe evidence
bookkeeping and certain closure writes may still be explicitly requested via their
normal API. `record_evidence` requires a registered revision target; it is not a bound
future payload. Eligibility is always `eligible_read_only_recheck_at_mutation`, never
a command, automatic resume or guarantee of future authorization.

## Additive `/state` schema evolution

The outer `schema_version` stays **1**. Every pre-existing v1 key remains, including
unchanged active-task and active-with-ancestors selection. The new `governance`
section has its own **schema_version: 1**, `as_of`, `event_cursor`,
`selection: active_with_ancestors`, `recovery` (same selected hierarchy order), and
`metrics` (all permitted project tasks, including terminal work). Consumers must
ignore additive fields and treat a missing section on older servers as unavailable.
Independent section versioning permits later governance evolution without redefining
the legacy active resources. Standalone task recovery includes terminal tasks omitted
from active selection. All dependent rows and the visible project event cursor share
one explicit SQLite read transaction; one captured timestamp drives all governance
budget projections. The cursor is not an observation ETag or a claim that no concurrent
writer has committed since the read snapshot was established.

## Metrics payload v1 and deterministic definitions

Metrics include `schema_version`, `as_of`, `event_cursor`, sorted `project_ids`,
`task_id`, `selection`, `window: all_persisted_history`, `thresholds`, `counts`,
`provenance`, `coverage`, per-task `budgets`, `orphan_task_ids`, `stalled_task_ids`,
`definitions`, source-labelled `runtime`, and `alerts` sorted by task ID then code.
All persisted relevant events/records are scanned, **not** `recent_events` or its cap.
Transport idempotent replays produce no additional metric events or authorization use.

Counts include tasks/completed/terminal tasks; known child creation events;
delegation records **including reparent authorizations**; governance denials,
classified delegation denials and unclassified legacy denials; contract initializations,
successful amendments and observed amendment requests; canonical/open exceptions,
recording events and explicit inspections; remediation attempt records and authorized
retries; registered/validated deliverables; and tasks with required outputs/satisfied
required outputs. Counts are absolute, with their populations explicit; there is no
implicit recent time window, inferred rate, or execution-completion percentage.

New replay-mediated audit events carry a sanitized `details.request_type` enum
(`delegation.create`, `task.create`, `contract.initialize`, `contract.amend`,
`remediation.request`, `remediation.authorize`, `task.transition`, `other`). Policy
denials retain `code` and governing revision IDs plus that operation class. Existing
events are never rewritten. Observed amendment requests are successes plus recorded
policy denials, **not** all HTTP attempts: auth, validation and stale-version failures
are not persisted. Legacy ambiguous denials are not classified from their code alone.

Historical child counts replay recorded creation/parent-change events at each birth
cursor. They mean **root at that child's creation**, not today's root or an assumed
original delegation root. Missing ancestry is explicitly unavailable. Current child
rows grouped by current root are separately labelled and may differ after reparenting;
reparent authorization records are not new child creations. Legacy missing creation
parent metadata and tasks with no creation event are separately counted; current
mutable lineage never fills history.

Canonical exceptions are one task/failure-kind record, not occurrence telemetry.
Inspections are not repeated failures. Observed exception recurrence is unavailable
because duplicate canonical recordings are not persisted. Attempts count remediation
records; authorized retries count only successful authorizations, not approval requests
or transport retries. Unknown runtime/context utilization is null with an unavailable
reason, never zero or estimated from tokens/conversation. Runtime status/heartbeat/
observation times are labelled `persisted_agent_sessions`, not live process evidence.

Alerts are observations, not enforcement or inferred intent. Fixed inclusive thresholds:

- `depth_limit_approached`: any applicable remaining descendant depth <= **1**.
- `repeated_denied_expansion`: >= **3** classified delegation denials for the task.
- `multiple_canonical_exceptions`: >= **3** distinct canonical exceptions; recurrence
  remains unknown (no unsupported repeated-occurrence alert is manufactured).
- `repeated_authorized_retries`: >= **3** recorded successful retry authorizations.
- `closure_missing_required_outputs`: missing current required keys on a terminal
  task, or with a recorded required-output closure denial. Safe failed/cancelled exits
  remain valid; this alert does not declare those transitions illegal.
- `stalled_unresolved_exceptions`: an open exception's last explicit inspection/
  observation (`last_seen_at`) is at least **3600 seconds** old, including paused/terminal
  tasks. This is a metadata-age signal, not proof of runtime inactivity.

An orphan is an active task with no registered owner or an ended owner. Unknown
heartbeat/status alone does not establish orphanhood. Lists/alerts/budgets are stable
for the same rows, caller grants and captured time. Reads never reset ages or counters.
No database migration is introduced by E; the schema remains revision **0006**.
