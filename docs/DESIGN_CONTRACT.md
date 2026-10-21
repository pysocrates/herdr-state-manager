# HSM v1 design contract

## Checkpoint E: deterministic recovery and full-history reporting

Task recovery is a pure authorized projection for all lifecycle states. State retains
every v1 key/active selection and adds an independently versioned governance section.
Rows, history and visible cursor share the caller's explicit read transaction; a single
captured time is passed into every governance authorization-window calculation. A
concurrent committed writer is seen wholly on a later snapshot, not partly in one read.
Neither an event cursor nor a projected eligible action is an execution lease/ETag.

Recovery carries objective, exact local/applicable immutable revisions/specifications,
per-owner limits/use/remaining budgets, durable revision-bound artifacts/checkpoints,
unresolved exceptions, ownership, pause and terminal state. Only a recorded checkpoint
action can be projected, conservatively revalidated against task/contract/evidence,
caller grants and current policy. Paused/terminal recovery always suppresses next action;
safe bookkeeping still uses the existing explicit mutation routes. Historical successful
mediated-action events are labelled as such, never interpreted as live authority.

Metrics scan all relevant persisted records/events, not the bounded recent-event view.
Immutable child-creation history is replayed with recorded parent changes to establish
the root at creation, only when supported by recorded ancestry. Current root grouping
is separate; unknown legacy provenance stays unavailable. No retroactive event rewrite
or fabricated original lineage is permitted. New replay-mediated events carry a sanitized
request class for reliable denial/amendment attribution. Request metrics explicitly cover
only successes and persisted policy denials, not unrecorded HTTP failures. Canonical
exceptions/inspections do not become observed recurrence. Missing runtime/context metrics
remain unavailable. Event replay has no additional contribution to counts or budgets.

Inclusive thresholds, denominators, orphan/stalled definitions, sorting and payload
fields are specified in API.md's checkpoint E section. Alerts cannot change state or
infer intent. Snapshot reads never contact adapters or external artifact storage.
No migration: existing 0006 records suffice. Work scales with retained history and tasks;
this version supplies exact read-derived results, not a precomputed analytics service.
Process execution/control and context-utilization estimation remain out of scope.

## Checkpoint D: reversible governance, not lifecycle or process control

Forward-only migration 0006 adds Task.governance_paused (non-null false for legacy
rows) and nullable latest actor/time/reason fields. No old migration is rewritten.
Operator pause/resume uses the existing serialized Service transaction, replay,
project grants, version checks and audit events. It cannot change terminal tasks.
Resume removes one local gate only, never resumes execution, changes lifecycle,
reassigns ownership, discards artifacts or resets authorization accounting.

Pause ancestry is independent of contract ancestry. The shared check_work gate
denies child creation, ordinary PATCH, work transitions, approval consumption and
remediation across the entire tree. Reparent checks cover source/destination and
paused descendants; contract amendments also require unpaused ancestry. Review
and completion are safe bookkeeping exits, retaining all ordinary lifecycle,
contract-window/prohibition and required-deliverable checks. Failure/cancel remain
available. Recording artifacts/evidence, exceptions and checkpoints is evidence
preservation, not permission to do external work; existing next-action checks apply.

Every ordinary task/hierarchy projection is enriched by the shared Service forest,
not a competing hierarchy API. Scope is a transaction-consistent authorized read
of full task, applicable immutable contract revisions, durable deliverables,
unresolved task exceptions and current count/time allowances. Direct child budgets
belong to each owner; depth is relative to the selected task, aggregate retries use
historical authorization lineage, and windows are elapsed wall-clock authorization,
not CPU/process usage. Null means unconstrained. Scope exposes per-owner constraints
so ancestor-specific allowances are not silently conflated. No runtime telemetry
is guessed. Decisions are read-only snapshots requiring mutation-time revalidation.

Runtime control is explicitly unsupported/unacknowledged. Existing adapters remain
observe-only; neither pause nor resume calls them. A noncooperating live runtime
may keep running. Full recovery and governance metrics are specified in checkpoint E
above; process execution metrics remain unavailable without authoritative telemetry.
See API v1's checkpoint D section for exact intervention response fields.

## Checkpoint C: durable evidence and controlled remediation

Revision 0005 adds Deliverable, DeliverableRevision, ValidationEvidence, Checkpoint,
CheckpointRevision and Remediation, plus nullable task/failure/description/resolution
metadata (including evidence_reference, proposed_action, scope_impact) and version=1
on existing Incident. Existing rows and migrations 0001–0004
remain untouched. Foreign keys bind project/task/artifact/evidence/approval records;
service validation additionally enforces same-task relationships. Public history is
immutable, not protected from trusted local DB administrators.

Artifacts are declared references and exact identity strings, never opened, hashed
from disk or executed by HSM. Every revision is independent, including identical
reference/identity repetitions; evidence binds one exact revision and identity.
Latest evidence outcome defines that revision's present validation state. Old evidence
and known-good checkpoint associations remain immutable. Task/session termination
cannot remove records. Metadata/evidence and checkpoint creation after failure is
allowed without resuming terminal work. Supersession is explicit, same-task, acyclic,
once-only, and excludes the superseded key from current readiness. It never aliases
one contract requirement to another key.

Required-deliverable completion uses the active **local task** contract, not a union
of ancestor requirements: parents retain their own obligations and no completion
cascades. Exact required keys must have current non-superseded deliverables whose
latest revision has latest passing evidence. With a nonempty required set,
stop_when_satisfied=true blocks new mediated work below that contract: child create,
ordinary PATCH, non-final transitions, approval consumption and remediation. Empty
sets cannot vacuously stop legacy work. Final review/completion are exempt from this
stop gate only (existing transition/prohibition/window rules remain); failure and
cancellation retain their safe-exit exemption. Evidence/exception/checkpoint recording
is bookkeeping, not new execution; no stop/window/prohibition blocks its recording.

Checkpoint creation is immutable and version-checks the task. Every supplied revision
must be same-task and currently known-good. It captures the exact validation evidence
record, current contract IDs (nearest first), task version, supplied blockers and
server-derived completed required keys represented by the supplied *current* revisions.
Historical revisions may be included without asserting they satisfy today's contract.
Next action is a constrained declaration: review/complete are checked against actual
policy/lifecycle; record_evidence checks for an existing same-task revision and
applicable record write/read grants, including on terminal tasks. Without an actual
revision target it is rejected. Null means unknown. The declaration is not a grant
for a particular future evidence payload (exact version/identity checks still apply). It
never substitutes for a fresh check when acting. Restoration is GET only, never a
resume, execution, fresh grant, or rollback of task state.

Task exceptions reuse Incident. Reconciliation types are unchanged. Canonical failure
identity is task UUID plus one of execution/validation/dependency/scope; prose and
client IDs do not create distinct failures. There is deliberately one exception per
task/kind for the task's lifetime. `/inspect` records an explicit audited inspection;
ordinary GET stays read-only. `/resolve` records note/time/version but grants nothing.
Optional nullable evidence_reference, proposed_action and scope_impact are strict
1..4096-character strings persisted on the Incident and creation audit, returned in
reads, never fetched/executed or treated as authority. Existing incidents default null.
Scope exceptions cannot authorize retries; out-of-scope work requires an operator
contract amendment or an independently authorized task, never an exception flag.

Only `remediation.retry` is supported; it authorizes an in-scope retry of the recorded
failure, not an arbitrary HSM mutation or command. An existing Approval must match
task, operation, target, retry action and a fingerprint of **all** current contract
revision IDs. The requester associates it; the requester alone consumes it through
atomic remediation authorization. Current tasks:write/read/approvals:consume grants,
project, current versions, terminal state, unresolved exception, runtime, declared
resource scope, operation prohibitions, satisfaction and ancestor budgets all apply.
Any consumption or authorization failure rolls back both domain records and events;
only sanitized policy denials and replay receipts survive savepoint rollback. Native
observations cannot authorize remediation. Generic approval consumption checks
policy too and refuses remediation grants; no route accepts a scope override.

The canonical rejected action is task + failure kind + fixed remediation.retry,
independent of approval/incident/key IDs and contract amendments. Matching rejected
Approval rows block even if never associated; linked rejection is reflected on the
Remediation row. Authorized attempts persist their original entire task ancestry;
active ancestor retry_budget counts lifetime successful authorizations sharing that
ancestor (across failure kinds), not pending requests, transport replay or current
active task count. Null is unlimited, zero forbids; closure and amendment never refill
usage. Changing ancestry or contract revisions between request and authorization
requires a fresh association/grant, not silent reuse. The independent nullable strict
integer `max_retries_per_failure` (0..1000000) counts only authorized records with the
same canonical task/failure/action identity in each applicable contract's recorded
ancestry. Legacy missing values mean null/unlimited, zero forbids. New grant IDs and
contract amendments cannot reset either counter. Prior use under then-uncontracted
ancestors counts when contracts are added; tightening grandfathers old authorizations
but denies further use at/above the cap. Distinct identities have separate per-failure
allowances but share the aggregate retry_budget. BEGIN IMMEDIATE serializes the
last-slot budget race and consumption. No subsequent lifecycle changes occur.

Limits: declarations are trusted facts, not semantic verification of reworded intent
or external content. Failure-kind reclassification cannot be semantically detected;
ancestor aggregate retry budgets still apply. A permanently rejected task/kind cannot
be reopened through this API. Conservative canonicalization intentionally merges
multiple failures of that kind. Detail histories are complete/unpaginated, while
collection pages are bounded. External availability, actual remediation execution,
process runtime and enforcement against noncooperating clients remain unknown.
CLI/TUI controls and aggregate governance recovery/metrics belong to later checkpoints.

## Checkpoint B: opt-in contract and delegation authority

Revision 0004 adds immutable-by-public-API contract revisions and delegation records.
There is no API for deleting or changing historical revisions; local database
administrators remain outside this trust boundary. Contracts require an operator
with `tasks:write`, explicit nonblank reason, expected task version and expected
contract revision (0 before creation), and an idempotency key. A successful full
replacement appends a revision and increments the task version. It records actor,
UTC timestamp, both expected versions and a persistent authorization time origin.
Terminal tasks remain immutable. Ordinary task writers cannot amend contracts.

### Exact enforcement

- Legacy tasks without a contract anywhere in their ancestry retain unrestricted
  task bookkeeping. Governance is opt-in, not an execution or ownership ACL.
- Every creation checks all ancestor contracts under the existing BEGIN IMMEDIATE
  transaction. `max_children` counts lifetime *direct-child authorization records*,
  including terminal children and prior attachments, not active tasks. Creating a
  child or attaching a legacy task consumes one record; replay consumes none.
  Amendment does not reset usage. `max_descendant_depth` measures edges relative
  to each ancestor with that limit (0 forbids descendants, 1 permits only children).
- Initial contracts and amendments validate all existing descendants' labels/depth
  and consumed direct-child records before mutation. Tightening below existing use
  is denied. Limits do not cascade lifecycle state or affect owner assignments.
- **Governed reparenting is intentionally unsupported in B.** PATCH rejects a
  changed parent if the source ancestry, destination ancestry, or any node in the
  moved subtree has a contract. This includes detachment and operator requests;
  no implicit waiver exists. Contract-free reparenting remains supported and records
  each attachment without overwriting original delegation provenance. Cycle and
  project-reference checks still apply first. This conservative rule avoids budget
  transfer or ancestry-stripping bypasses rather than silently interpreting a move.
- Task `resource_labels` and non-null repository/worktree path strings form the
  declared resource set. This set must be a subset of each applicable contract's
  `resource_labels` by **exact case-sensitive string equality**. No normalization,
  prefixes, globs, filesystem traversal, fetch, or path access occurs. Thus `repo/x`
  does not match `repo`. Null allowed labels means unrestricted; [] means no labels
  or path strings. Duplicates have set semantics. Base commit is pinned metadata,
  not a resource label. Undeclared real-world resource use cannot be observed.
- Under any contract, ordinary PATCH cannot change objective, resource labels,
  repository/worktree paths or base commit. Explicit operator contract revision
  updates those fields atomically. Its allowed-label list also replaces the task's
  declared labels when non-null; null preserves its declared labels. Omitting paths
  in a full replacement clears them. Child objectives and spawn reasons are bounded
  declarations, **not semantically verified** against natural-language parent goals.
- `runtime_seconds` is an elapsed UTC wall-clock **authorization window**, beginning
  at the first contract revision's persisted `authorization_started_at`. Amendments
  preserve that origin (including when the initial limit was null), restart does
  not refill it, and expiry is elapsed >= limit. Each ancestor's window applies.
  Amendments can extend/remove their own limit explicitly, but cannot bypass an
  expired ancestor. Creation, ordinary PATCH and transitions are denied after
  expiry; valid failed/cancelled transitions remain available. No CPU accounting,
  timers, process kills or automatic state transitions exist. Host clock accuracy
  is assumed; this is not a monotonic process clock.
- Null numeric limits mean unlimited; zero is enforced; negative values, boolean
  integers, unknown fields and oversized collections are rejected. Counts/depth/
  retry budget max 1,000,000; runtime max 315,360,000 seconds; lists max 100 items.
- `prohibited_operations` defaults to [], bounded to 100 nonempty labels of at most
  200 characters. Exact case-sensitive enforced identifiers: `task.create` for
  child creation, `task.patch` for ordinary PATCH, `task.transition` for all
  transitions, and `task.transition.<state>` for each defined lifecycle state.
  The task and all contracted ancestors apply (ancestors only for creation).
  Valid failed/cancelled exits remain available even if explicitly prohibited.
  Unknown labels are advisory; no glob, shell, tool or external runtime enforcement
  is claimed. Operator contract amendments and other endpoints are not these operations.
- `expansion_requires_authorization` is a strict boolean defaulting true. Even false
  grants no automatic expansion: all amendments still require an explicit operator.
  `stop_when_satisfied` is a strict boolean defaulting true, persisted only in B.
  Older stored specifications missing these fields retain effective [], true, true
  defaults without rewriting history; operation checks use explicit `.get` defaults.
- `retry_budget`, `stop_when_satisfied` and `required_deliverables` are persisted contract declarations
  **only in B**. Stable failure identity, remediation accounting and deliverable
  completion checks depend on checkpoint C. B does not block completion for missing
  deliverables and cannot observe external retries. Approvals remain a separate
  authority; B does not make approval consumption an execution/policy gate.

### Durable denial and lineage

Policy denial is a narrow internal exception handled by a savepoint rollback,
followed by a sanitized `governance.denied` event (reason code and governing revision IDs, authenticated
actor, project/task and timestamp). Authorization and relationship checks precede
policy checks. Other exceptions still roll back normally. Idempotent POST denial
responses are cached just like successes; replay emits no new event. PATCH keeps
its existing non-idempotent transport semantics, so a repeated denied PATCH emits
another denial. No rejected objective/path/spawn-reason payload is copied to audit.

All new task-associated events persist `details.governing_contract_ids` at write
time, nearest contracted task first followed by every contracted ancestor. Denials
use the restored authoritative ancestry after savepoint rollback within the same
serialized writer transaction. Amendments identify the new revision in both this
list and `contract_id`/`revision`. Later amendments do not reinterpret prior events.
An empty list means no applicable contract; absent fields on preexisting events
mean unknown historical attribution. No inferred backfill or JSON migration occurs.

Delegations persist original parent, direct-parent revision (nullable), all governing
revision IDs, authenticated `authorized_by`, spawn reason, and timestamp. Migration
backfills surviving legacy links with actor `migration:unknown`; it cannot reconstruct
removed pre-0004 links or their historical authorization, and does not pretend to.
Lifetime budgets include this baseline plus all post-0004 attachments. Task views
expose `delegation_history`; `delegation_depth` is absolute even when subtree display
`depth` is normalized. Current ancestry and historical provenance are distinct.

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
