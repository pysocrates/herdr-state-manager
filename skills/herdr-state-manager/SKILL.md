---
name: herdr-state-manager
description: Register and update HSM tasks, agent ownership and Herdr pane assignments; recover project state after Hermes context resets; request exact-action operator approvals. Use when working with an existing Herdr State Manager service, not for scheduling agents or managing the HSM installation.
license: MIT
---

# Herdr State Manager

HSM records durable state; the Hermes orchestrator still plans, delegates and executes.
A request to register work authorizes bookkeeping, not executing that work. Runtime
idle/done status and approval decisions never establish task completion.

## Connect and recover first

Use the configured `HSM_API_URL` (default `http://127.0.0.1:8787`) and a scoped **agent**
credential via `HSM_TOKEN_FILE` or `HSM_TOKEN`. Read secrets inside the request process;
never print tokens, inspect the server's credential store, source an operator environment,
or switch to operator credentials after a denial. If no agent credential is configured,
request its private file path or provisioning; do not guess deployment paths.

If HSM MCP tools are available, use them; they reach the same authenticated API.
Otherwise use `scripts/hsm_request.py` from this skill directory. It needs only Python
3.12+ and outputs JSON. Paths below are relative to that directory; resolve the actual
skill location before invoking them. Read [references/workflows.md](references/workflows.md)
for concrete commands and fields.

Start with `GET /state` (or `/state?project=SLUG` via `--query project=SLUG`). Inspect
projects, active task keys/versions, owners, panes, approvals and warnings. For a harmless
demo with exactly one authorized project, use that project; do not invent a project
or create administrative resources. Resolve ambiguous project scope before writing.
For an old or terminal task absent from active_tasks, use the paginated `/tasks` list.

## Register only what is known

- Task creation needs project_id, task_key, title, objective and a reusable logical
  operation idempotency key. Newly created tasks are queued. GET the returned UUID
  independently to verify registration. Check existing keys before creating duplicates.
- Agent profiles and sessions differ. Register a session with the actual known profile;
  external_session_id is optional. Unknown profile/session identity is not a reason to
  fabricate one. Preserve the returned internal UUID for ownership.
- Assign owner_session_id with a task PATCH using the current expected_version.
- Inspect existing pane registrations before creating one. Link the correct known
  pane through its **internal HSM UUID**, not its external Herdr pane ID. Use confirmed
  runtime identifiers; do not match identities from titles, working status or names.
- A reported Herdr Hermes session reference of kind=id can confirm identity. A null
  reference cannot. Do not query entire conversations to discover identity.
- Parents, owners, pane assignments and approvals must stay within one project.

Session/pane creation has no idempotency guarantee. After an ambiguous response,
inspect registrations before retrying; duplicate session records are otherwise possible.

## Larger tasks and delegated subtasks

Keep project_id for the software project; link each delegated work item with parent_id
pointing to the larger task's internal UUID. Give each child its actual owner_session_id
and pane linkage. A child may itself have children. HSM records delegation; it never
spawns workers. Task keys remain unique within the project.

Inspect `/tasks/UUID/tree` or hsm_task_tree for full progress, including terminal
children; use `/tasks?parent_id=UUID` for paginated direct children. Snapshot task_hierarchy
retains active work and ancestors, with counts across closed branches. Failed/cancelled
work is separate from completed counts. Parent/child transitions never cascade; update
each task explicitly based on actual progress. Read the
[decomposition examples](references/workflows.md#larger-tasks-and-agent-owned-subtasks)
when delegating or attaching existing work.

## Task contracts and governed delegation

Before delegating beneath a task, read `GET /tasks/UUID/contract` for that task and
its ancestors. The response contains `current` (null for no local contract) and
immutable `history`. An uncontracted child may still be governed by an ancestor.
The helper supports this read; it deliberately does not expose operator-only
contract writes. Contract registration/amendment uses an operator's explicit
`POST /tasks/UUID/contract`, with expected task and contract versions, full contract
and reason. Ask the operator to review that action; never borrow their credential.

Create children through the existing `/tasks` endpoint with `parent_id`, a concrete
`spawn_reason`, and declared `resource_labels` where relevant. HSM checks cumulative
direct-child and ancestor-relative depth budgets atomically. Paths/labels are exact
opaque strings, not glob patterns or filesystem permissions. Child completion or
cancellation does not replenish delegation allowance. Inspect `delegation_history`
for original authorization and governing contract revision IDs.

On a governance denial, inspect current contract/history and report the denied
operation. Do not evade it by detaching, reparenting, changing keys or omitting
known resource declarations. Governed reparenting is currently unsupported. Changes
to governed objective/path/resource metadata require an explicit operator contract
revision. HSM cannot detect arbitrary external tool use or interpret objective text.
Its authorization window is elapsed host UTC time, not process execution accounting.

## Durable deliverables, checkpoints and exceptions (checkpoint C)

All new writes below require `tasks:write`, current project grants,
`--idempotency-key` and the documented `expected_version`. Replay requires the same
payload/key; read back independently. The helper exposes these agent routes, never
operator contract amendments or approval resolution.

- `POST /tasks/UUID/deliverables`: task expected_version, key (matching an explicit
  required_deliverables entry), title, reference and artifact_identity. References
  are metadata only; prefer a content hash or immutable revision as identity.
- `POST /deliverables/UUID/revisions`: deliverable expected_version, reference,
  artifact_identity. Every revision starts unvalidated, even if the identity repeats.
- `POST /deliverables/UUID/evidence`: deliverable expected_version, exact revision_id,
  artifact_identity, outcome (`passed`/`failed`), reference to validation evidence.
  Old evidence cannot validate a new revision. No files or commands are opened.
- `POST /deliverables/UUID/supersede`: expected_version and superseded_by UUID from
  the same task. Superseded keys no longer satisfy requirements; no implicit alias.
- `POST /tasks/UUID/checkpoints`: task expected_version, revision_ids (only known-good
  same-task records), optional blockers and next_authorized_action (`review`,
  `complete`, `record_evidence`, or null). All non-null actions are validated, never
  executed: review/complete require current lifecycle/policy eligibility; record_evidence
  needs an existing same-task revision and evidence grants, even on terminal tasks.
- Recover with `GET /deliverables`, `/deliverable-revisions`, `/validation-evidence`,
  `/checkpoints`, `/exceptions`, `/remediations`, adding `--query task_id=UUID` and
  bounded pagination. Each supports `/UUID` detail. Artifacts survive terminal tasks.
  Checkpoint GET is read-only restoration; it never resumes or authorizes new work.
- `POST /tasks/UUID/exceptions`: task expected_version, failure_kind (`execution`,
  `validation`, `dependency`, `scope`) and description. Task/failure kind is canonical;
  a new key or reworded description cannot create a new retry allowance.
  Optional evidence_reference, proposed_action and scope_impact are nullable strict
  strings of 1..4096 characters, persisted in reads and creation audit. They never
  execute, grant authority or override scope; do not put secrets in these fields.
- `POST /exceptions/UUID/inspect` records inspection using incident expected_version.
  `POST /exceptions/UUID/resolve` also needs note; resolution grants no authority.
- For an in-scope retry, copy the exact `remediation_grant` from exception detail
  into an ordinary `/approvals` request plus project_id and expiry. Wait for the
  operator's explicit approval. Associate its approval_id with
  `POST /exceptions/UUID/remediations` and incident expected_version, then use
  `POST /remediations/UUID/authorize` with remediation expected_version and
  expected_approval_version. These require approvals:consume too, original requester
  identity, current contract revision binding, runtime/prohibitions and retry budgets.
  Authorization consumes the grant and appends retry use atomically, but does not
  execute anything or transition a task. Never consume remediation through the generic
  approval route. Rejected equivalent work stays rejected with new grant/key IDs.
  `retry_budget` is the aggregate lifetime authorization cap across recorded ancestry;
  `max_retries_per_failure` separately caps each canonical task/failure/action identity
  across all applicable contracts. Both are nullable strict integers 0..1000000;
  missing legacy per-failure values mean unlimited, zero forbids. New approvals,
  amendments, exception closure and task termination never refill usage. Earlier use
  counts when an ancestor first adds a contract; both checks/consumption are atomic.

Required deliverables gate completion using the active local contract's exact keys.
A nonempty fully validated required set with stop_when_satisfied stops new mediated
work under that task/ancestors; it does not automatically complete anything. Final
review/completion (subject to existing policy), safe failure/cancellation, evidence
and exception recording remain available. Empty required sets do not stop legacy
work. Scope expansion always needs an explicit operator amendment or separately
approved task; neither exception resolution nor approval grants an implicit waiver.

## Lifecycle and retries

Use dedicated `/tasks/UUID/transition` requests; task PATCH cannot set state.
All task edits/transitions require expected_version. Read
[references/workflows.md](references/workflows.md#lifecycle-and-conflicts) for allowed edges.
On 409, re-read and reconcile intent; do not blindly increment the version or bypass
planning/review. Terminal tasks remain terminal; create a linked follow-up when authorized.

Task creation, transitions and approval creation require Idempotency-Key. Retain the
same key **and identical payload** for an ambiguous retry within 24 hours. Use a new key
for a different logical operation. Do not automatically retry non-idempotent writes.
On 401/403, fix configuration/grants with the operator; on 503, a bounded retry of an
idempotent operation may be appropriate. Stop and report unresolved failures.

## Approvals and handoff

Read [references/approvals.md](references/approvals.md) when an authorized workflow
needs operator permission. Request an exact action, target and immutable artifact with
expiry. Agents never resolve their own approvals. Approval resolution, task resumption,
authorization consumption and actual execution are separate operations. Consumption is
single-use and has no automatic retry. No helper in this skill executes the action.

Before handing off or losing context, verify HSM reflects actual task state, ownership,
pane relationships and unresolved approvals. Recovery starts from a fresh snapshot;
respect runtime-source warnings and explicit history truncation. Keep verified project
knowledge in the external knowledge base. Do not store transcripts or secrets in HSM.
Report confirmed records/versions and missing links; do not claim execution from bookkeeping.
