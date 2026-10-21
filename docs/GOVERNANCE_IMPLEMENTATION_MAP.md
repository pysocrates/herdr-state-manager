# Agent governance implementation map — checkpoint A

Historical checkpoint A plan: the status and baseline observations below describe
the pre-implementation checkout. See [checkpoint F](CHECKPOINT_F.md) for final
acceptance, integration evidence and remaining limitations.

Status at checkpoint A: reconnaissance complete; implementation checkpoints B–F not started.
Baseline: `8174a68` (clean checkout before this document). This is an implementation
map, not a claim that the proposed features exist. No live service, credentials,
application repositories, or deployed database were changed during reconnaissance.

## Inspected architecture and existing overlap

- `models.py`: Project, Task, AgentSession, Pane, append-only API Event, Idempotency,
  Incident, AdapterHealth, Approval and separate native ApprovalObservation/Receipt.
  Tasks already have project-local `parent_id`, explicit session ownership, version,
  lifecycle timestamps and opaque Git/path metadata. There is no separate Agent model.
- `services.py`: shared authorization and project checks; task creation/edit/transition;
  relationship/cycle checks; actor-attributed events; idempotent response storage;
  project forests and consistent recovery construction. Existing action grants are
  explicit for agents; operator-only checks additionally enforce role.
- `database.py`: SQLite foreign keys, WAL, bounded busy timeout; every writer uses
  BEGIN IMMEDIATE. Read transactions use explicit BEGIN. Reuse these boundaries;
  do not add a second lock manager or counters updated outside the transaction.
- `app.py` / `schemas.py` / `responses.py`: authenticated Flask routes, strict Pydantic
  request inputs, existing success/error envelopes, snapshot response validation.
  Readiness currently requires Alembic revision 0003. Metadata/request sizes are bounded.
- `approvals.py`: expiring, exact-action, original-requester, single-use approvals;
  only operators resolve. Decisions neither execute nor transition tasks. Native
  observations are not grants. Keep these mechanisms distinct.
- `hierarchy.py`: iterative deterministic preorder, derived root/depth, direct and
  descendant progress. Existing reparent PATCH checks versions, cycles and projects.
  CLI tree/subtask commands and TUI indented task rows already exist. The TUI is not
  yet an expandable hierarchy and has no governance intervention controls.
- `reconciliation.py` / `adapters/*`: optional cached read-only observations, deduplicated
  incident opening/resolution, adapter availability and stale-heartbeat signals.
  Herdr uses a fixed read-only argv (no shell); Hermes reads selected metadata only.
  Preserve those established observation interfaces, not process control.
- `cli.py`, `client.py`, `tui.py`, `mcp/server.py`: normal clients use authenticated REST.
  CLI local init/backup/reconcile are existing administrative operations, not task work.
- Migrations 0001–0003 are frozen; `pyproject.toml` explicitly packages migration files.
- README, architecture/design/API/operations/integration documentation and the portable
  `skills/herdr-state-manager` skill describe the existing authority boundary.

### Invariants and compatibility constraints

1. Existing transition matrix and terminal immutability remain unchanged. Parent/child
   lifecycle does not cascade; telemetry is not completion evidence.
2. Task PATCH and transitions require expected_version. Creation/transitions/approval
   creation use identity + route + key idempotency with payload hashing and 24-hour
   retention. PATCH and approval resolve/consume currently have different retry
   semantics; do not silently rewrite them. New mutating governance operations require
   explicit idempotency and optimistic concurrency.
3. Contract-free work retains existing behavior. Opt-in governance must not silently
   prevent legacy root creation or existing linked follow-up work.
4. Snapshot rows and event cursor share one read transaction. Current schema_version=1
   exposes active tasks plus ancestors, not all terminal work; terminal-task recovery
   needs an explicit task-scoped read. Do not silently change active_tasks semantics.
5. Permission grants are project/action-based, not task ownership ACLs. Validate every
   relationship on the server; do not invent a new owner-only authorization policy.
6. Event append-only protection is the public service/API boundary, not protection from
   local database administrators. No API edits/deletes of historical evidence.

## Sequential implementation map

### B — Phases 1 and 2: contracts and delegation lineage

Extend `models.py`, `schemas.py`, `services.py`, `app.py`, `hierarchy.py`, tests and
existing API/design docs. A small governance domain helper is reasonable only if it
is called by the established Service mutation paths, not a parallel service framework.

Database: forward migration 0004 for immutable task contract revisions (unique task +
revision; full validated contract; actor/time/reason), delegation provenance referencing
the applicable parent revision, and supporting task/parent indexes. Reuse parent_id;
derive current root and depth. Preserve original provenance in events when reparenting.
Update migration packaging and readiness together. No existing migration edits.

API: authenticated contract current/history GET and explicit version-checked revision
creation POST under `/tasks/<id>/contract`; extend existing child POST rather than add
another creation path. Record spawn_reason, authenticated authorized_by and governing
parent revision; do not trust caller-supplied authorizer identity. Initial/amended
contracts require explicit authority. Prefer existing tasks:write plus operator role
for contract authority rather than giving ordinary task writers amendment privileges.

Semantics to specify in the phase design/tests before coding:
- Null limit means unspecified/unlimited; zero is a real zero limit. Reject negatives,
  booleans-as-integers, unknown fields and overlarge collections.
- Count consumed delegation authorizations, not merely currently active children;
  completion, cancellation or reparenting must not refill a budget accidentally.
  Specify direct-child allowance and ancestor-relative maximum descendant depth.
- Check ancestor constraints and entire moved subtrees, not only a new direct parent.
  Reparenting/detachment and ordinary objective/path PATCH must not bypass an active
  contract. Legacy uncontracted reparenting remains supported.
- Contract scope resource labels are opaque data, not filesystem access controls.
  Any label subset matching must be explicitly documented (no inferred path/glob
  containment). HSM can enforce only declared resources and mediated operations.
- Runtime seconds must have a documented persisted time origin and mean an HSM
  authorization window, not observed CPU/process time. No automatic process kill.
- Retry budgets need stable persisted failure identity; integrate enforcement with
  exception/remediation operations in C. Do not claim shell retry observation in B.
- Required deliverable completion enforcement becomes operational in C. B records
  requirements but must clearly disclose that dependency until C is verified.

Critical transaction detail: raising Problem currently rolls back the whole write.
A denial event inserted before raising would disappear. Add a narrowly scoped denial
result path that commits only sanitized audit/idempotency records after validation,
without task mutation; never make arbitrary error transactions commit. Validate project
visibility first so denials do not disclose resources to unauthorized principals.

Tests: contract create/history/immutable amendments; authorization; stale/concurrent
revisions; invalid values; concurrent child creation at the last slot; depth/cycles/
cross-project checks; replay not double-counting; reparent/detach bypasses; preserved
provenance; committed sanitized denials; migration of existing data and legacy tests.

Completion: HSM-mediated delegation is atomically allowed/denied by applicable
contracts, lineage is inspectable, relevant tests and docs pass. Expandable TUI is D.

### C — Phases 3 and 4: deliverables, checkpoints, exceptions

Extend the same models/schema/service/routes and `approvals.py`; reuse Incident for
execution exceptions, keeping reconciliation incident behavior compatible. Extend
incident type/task metadata and resolution tracking rather than add a competing
approval mechanism. Native approval observations remain observational only.

Database: next forward migration for deliverables with immutable revision/evidence
history and explicit supersession; immutable checkpoints referencing exact contract
and deliverable revisions; incident exception metadata and approval associations;
stable remediation identity/attempt records where necessary. All have project/task
foreign keys and appropriate indexes. Existing incident rows retain valid defaults.

API: deliverable create/list/detail/revise/supersede; checkpoint create/history/detail;
exception record/list, remediation request/approval association and resolution. New
writes are authenticated, project-checked, idempotent, versioned and audited. Existing
approval resolution/consumption is the authorization authority, not exception status.

Rules: artifact references are bounded metadata, never fetched/executed by core.
Identity changes reset validation or create a new immutable revision. Known-good
checkpoint references must resolve to appropriate recorded revisions and ownership.
Task/session failure never deletes artifacts. Contract completion matches explicit
required deliverable identifiers, not natural-language interpretation. Missing external
files cannot be detected from opaque references; distinguish missing registry records
from unverified external availability.

Remediation uses a durable task/failure/action identity, not a client-chosen request ID.
Rejected equivalent attempts remain rejected across new idempotency/approval identifiers;
changed identifiers do not grant work. HSM cannot infer semantic equivalence of arbitrary
reworded intent: out-of-scope work still needs an explicit contract amendment or separate
authorized task. Approval alone must not silently relax the original contract. Record
retry use atomically against stable failure identity, within active budgets.

Tests: durability after task/session failure; exact revision validation and supersession;
ownership/auth; immutable history; checkpoint references/read-back after restart;
completion with missing requirements; exception lifecycle, duplicate remediation/rejection,
approval expiry/consumption, budget races, and unchanged terminal semantics.

Completion: failed sessions cannot erase outputs; exceptions cannot authorize scope
expansion; tests/docs pass independently of later UI work.

### D — Phase 5: operational controls and adapters

Extend `cli.py`, `tui.py`, `client.py` only as needed, service/routes and optional adapter
interfaces. Preserve existing tab/navigation and bounded approval-history requests.
Use an expandable hierarchy within the current terminal UI; show budgets, exceptions,
deliverable readiness, session ownership and existing adapter health. Do not build a GUI.

Database: additive task governance pause metadata (separate from lifecycle), actor,
reason and versioned audit, in a forward migration if not already introduced by B/C.

API/CLI: authenticated scope inspection, checkpoint inspection/creation and explicit
pause/resume POST. Use existing task ID/key resolution for `hsm scope`, `checkpoint`,
`pause`, `resume`; mutations accept expected version, reason and idempotency key.
Operator-only intervention; preserve session ownership and artifacts. Gate new governed
work and approval consumption through affected ancestry; permit evidence recording,
exception reporting and safe terminal cancellation/failure. Resume is not restart.

Expose read-only governance decisions to cooperating integrations through REST. Existing
adapters observe only and cannot acknowledge process interruption; report unsupported
control honestly. No spawn/kill/shell interfaces, mandatory adapters, or automatic live
configuration changes. TUI controls must confirm actions and surface denial/stale errors.

Tests: CLI transport/read-back, operator vs agent intervention, paused ancestor bypasses,
preserved artifacts/ownership, terminal states, Textual expand/collapse/details/actions,
filter changes/stale views/bounded requests, no-op and unsupported runtime behavior.

Completion: operators can inspect and persist interventions from CLI/TUI; no claim that
live agents have been stopped.

### E — Phases 6 and 7: recovery and deterministic metrics

Extend `Service.snapshot`, `responses.py`, app read routes and hierarchy serialization;
keep all dependent reads inside the existing transaction. Add task-specific recovery
for any lifecycle state, plus an additive versioned governance section to the existing
snapshot. Retain v1 keys and describe schema evolution explicitly.

Recovery fields: exact active contract/revision, objective, constraints, remaining budgets,
revision-bound deliverables, unresolved exceptions, pause/lifecycle/owner state, latest
checkpoint, last recorded authorization, and explicitly recorded next authorized action.
Unknown action/telemetry remains null, never inferred from titles or conversation. A
checkpoint proposal must not be presented as authority without validation. One visible
event cursor and as-of timestamp bound the snapshot. Reads never resume work.

Metrics: authenticated project/task read view derived from all relevant persisted events
and records, never only recent_events. Define denominators/windows and deterministic
thresholds; distinguish operational retries from idempotent transport replay. Child
creation per root must use historical provenance, not rewritten current tree alone.
Expose rejection/amendment/exception/remediation counts, deliverable vs task completion,
budget use, and defined orphan/stall signals. Alerts are observations, not model intent.
Runtime-derived measures identify source and are unavailable without sufficient telemetry.
No new infrastructure or mandatory LLM dependency; no persistence needed for derived
metrics unless a required source event is missing from an earlier checkpoint.

Tests: restart/session replacement; incomplete/paused/terminal tasks; exception and
artifact recovery; consistent concurrent reads; stable sorting/schema; no read mutations;
metric counts unaffected by recent-event truncation; threshold fixtures, replay exclusion,
project isolation and unknown telemetry.

Completion: a replacement orchestrator can recover persisted constraints/evidence without
conversation reconstruction; deterministic anomalies have reproducible evidence.

### F — integration and documentation

Run full suite, migration upgrade tests on populated 0003 fixtures, packaging/readiness
checks, authenticated HTTP and CLI/TUI integration against temporary databases and
mocked runtimes. Review all alternate mutation surfaces including MCP and skill helper.
Do not migrate production or install/restart services.

Update README, docs/API.md, ARCHITECTURE.md, DESIGN_CONTRACT.md, OPERATIONS.md,
VERIFICATION.md, HERMES_INTEGRATION.md where applicable and the repository companion
skill/reference examples. Teach contracts/delegation/denials/exceptions/deliverables/
checkpoints/recovery with scoped credentials. Separate enforced mediation from advisory
runtime behavior and operator-approved deployment from source implementation.

Completion: complete regression/migration/security review passes, known limitations are
listed, working checkpoints are independently committed where workflow permits. No push
or deployment is included.

## Execution and review discipline

Each checkpoint report records summary, exact changed files, migration revisions, real
test commands/results, remaining limitations and COMPLETE/BLOCKED/FAILED. Commit only
verified work, separately by checkpoint. Within B/C retain independently reviewable
phase changes and focused verification. One implementation owner per shared backend
file set; concurrent workers may own disjoint tests/docs only after interface agreement.
Delegations require parent objective, files, measurable criteria and test evidence;
no recursive incidental work. On failure: record it, one narrow diagnostic, fix only
within phase scope or stop that affected task with the blocker.

## Checkpoint A verification

- Repository remote verified as https://github.com/pysocrates/herdr-state-manager.git.
- Original worktree clean; latest local commit 8174a68. No remote update/push performed.
- Existing environment baseline: `.venv/bin/python -m pytest -q`
- Result: **130 passed in 8.64s**; temporary test databases/mocked integrations.
- Changed files: this implementation map only. Database migrations introduced: none.
- Remaining limitations: B–F are unimplemented; no governance acceptance claim yet.
