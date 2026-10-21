# Checkpoint C — durable work and controlled remediation

**Status: COMPLETE — parent independently reviewed and verified.**
Base: checkpoint B commit `ae8c4db`. This report covers the entire uncommitted C
worktree, including the preceding worker's implementation and this acceptance pass.
No commit, deployment, live database migration, service restart or recursive delegation
was performed. All database execution below used temporary test databases.

## Implemented behavior

### Durable deliverables and checkpoints

- Project/task-bound Deliverables have immutable revisions, declared opaque references
  and exact artifact identities. Every new revision starts unvalidated, even with a
  repeated identity. Evidence binds an exact revision and identity; latest recorded
  outcome determines present validity without erasing earlier evidence.
- Explicit supersession is one-time, same-task and acyclic. Superseded keys do not
  satisfy requirements or implicitly alias replacement keys. Version checks and
  idempotent writes preserve concurrency/replay semantics.
- Independent detail and paginated collection reads expose artifacts, revisions,
  evidence, checkpoints, exceptions and remediation records, including terminal tasks.
  Task/session failure does not erase artifacts. Restart recovery is read-only.
- Immutable checkpoints capture task version, governing contract IDs, known-good
  revision/evidence associations, blockers and server-derived completed requirements.
  All accepted non-null next actions are checked: `review` and `complete` use actual
  transition/policy checks (including completion requirements); `record_evidence`
  requires an existing same-task revision and evidence write/read grants. The latter
  remains valid on terminal tasks because evidence recording is bookkeeping.
  Missing evidence targets return `evidence_target_missing`. Null remains unknown.
- Checkpoint reads do not restore task state, execute an action or grant future
  authority. Future evidence submissions still validate their exact payload/version.
- Local required-deliverable keys gate completion. A nonempty satisfied requirement
  set with stop_when_satisfied gates new mediated work through ancestry. Empty sets
  do not vacuously stop legacy work. Final review/completion bypass only this stop
  gate; other constraints remain. Failure/cancellation and evidence bookkeeping stay
  available; lifecycle never cascades.

### Exceptions and remediation

- Task exceptions extend existing Incident without changing reconciliation types.
  Canonical failure identity is task UUID plus the fixed execution/validation/
  dependency/scope kind. One exception per task/kind is deliberately conservative.
  Explicit inspect/resolve operations are versioned, idempotent and audited.
- Optional nullable `evidence_reference`, `proposed_action`, and `scope_impact` are
  strict strings of 1..4096 characters when supplied. They persist on Incident,
  exception reads and the exception.recorded creation audit. They are never fetched,
  executed or interpreted as authorization, scope overrides or alternative actions.
  Existing incidents receive nulls. Project access is checked before creation/read.
- Only fixed `remediation.retry` is supported. It reuses Approval: exact task/action,
  requester, expiry, unused approved status and current contract revision fingerprint
  are checked. Association and authorization require current grants. Generic standalone
  approval consumption rejects remediation grants and also checks applicable policy.
- Rejected matching approvals block canonical equivalent attempts even if never
  associated with a remediation record. Fresh approval/incident/idempotency IDs or
  altered prose cannot erase this rejection. Exception resolution grants no authority.
- Scope failures cannot authorize retries; they require an explicit operator amendment
  or separately authorized task. Terminal work cannot consume grants or be resumed.
- `retry_budget` retains its aggregate **lifetime authorization cap** across recorded
  ancestry, failure kinds and descendants. New nullable `max_retries_per_failure`
  independently caps the lifetime authorized count for the canonical task/failure/
  fixed-action identity at every applicable local and ancestor contract. Both accept
  only strict integers 0..1000000 or null; zero forbids, null is unlimited. Legacy
  stored JSON missing the new field defaults effectively to null without rewriting it.
- Original entire task ancestry is persisted in each attempt, including then-uncontracted
  ancestors. Earlier authorized use counts when an ancestor first adds a contract.
  Amendments, new grants, resolution and task closure never refill counters. Tightening
  can grandfather existing authorizations but forbids new use at/above either cap.
- BEGIN IMMEDIATE serializes both cap checks, approval consumption and ledger update.
  Failed consumption/authorization rolls back domain records/events; only sanitized
  policy-denial evidence and replay receipts survive the existing savepoint boundary.
  Authorization is recorded permission, never execution or a lifecycle transition.

## Exact file inventory relative to B

New files:
- `migrations/versions/0005_durable_work.py`
- `src/hsm/durable.py`
- `tests/test_durable.py`
- `tests/test_durable_acceptance.py`
- `docs/CHECKPOINT_C.md`

Modified files:
- `src/hsm/models.py` — durable models and nullable Incident metadata/version.
- `src/hsm/schemas.py` — strict durable inputs and max_retries_per_failure.
- `src/hsm/services.py` — completion/governance integration and incident filtering.
- `src/hsm/governance.py` — inherited satisfaction/completion policy integration.
- `src/hsm/approvals.py` — policy/terminal checks, rejected association handling and
  prevention of standalone remediation consumption.
- `src/hsm/app.py` — authenticated durable routes and readiness revision 0005.
- `pyproject.toml` — packages migration 0005.
- `tests/test_governance.py` — C compatibility and strengthened populated-0003
  preservation/integrity/repeated-upgrade checks.
- `tests/test_approval_observations.py` — current schema-readiness expectation.
- `tests/test_skill.py` — helper route and authenticated durable integration checks.
- `skills/herdr-state-manager/scripts/hsm_request.py` — allowed agent durable routes
  and idempotency requirements, without operator contract/approval-resolution exposure.
- `skills/herdr-state-manager/SKILL.md` — durable workflow, both cap semantics,
  metadata bounds/no authority and all checkpoint action validation semantics.
- `docs/API.md` — exact endpoints, fields, constraints, limits and authority boundary.
- `docs/DESIGN_CONTRACT.md` — persistence, enforcement, ancestry and recovery semantics.

This acceptance pass specifically changed schemas/models/durable/0005, added
`test_durable_acceptance.py`, strengthened existing durable/governance migration tests,
and updated API/design/repository skill documentation. Other C changes above were
inherited from the previous worker and are covered by the final full-suite run.
No committed migration 0001–0004 was modified. The repository skill has no remaining
stale B-only deliverable/retry limitation; historical B reports remain historical.

## Migration and compatibility

0005 follows 0004 and creates deliverables, deliverable_revisions, validation_evidence,
checkpoints, checkpoint_revisions and remediations, with foreign keys/indexes and
uniqueness constraints. Incident gains nullable task/failure/description/resolution
and evidence_reference/proposed_action/scope_impact columns plus version default 1.
The task FK uses SQLite's supported inline nullable REFERENCES added column; this
avoids Alembic splitting the FK into unsupported SQLite ALTER CONSTRAINT operations.
The preceding worker diagnosed/fixed that issue before this acceptance pass; there
were no failing test/tool runs requiring diagnosis in this pass.

Only the new uncommitted 0005 was extended. ContractSpec is stored in existing JSON,
so max_retries_per_failure requires no extra table migration. Readiness and packaging
require/include 0005. Populated tests compare all original columns/rows before and
after upgrade, repeat upgrade, assert foreign_key_check empty and integrity_check ok,
and verify final revision 0005. The 0003 fixture also checks original hierarchy and
migration provenance; the 0004 fixture includes tasks, contracts, incidents, approvals
and events, and verifies legacy incident defaults and the added task foreign key.
Fresh migration execution is exercised throughout API fixtures and the repeat/settings
test. No deployed database has been touched; this mutable 0005 must not be treated as
an upgrade for any external database already independently stamped with older 0005.

## Verification on final code/tests

All commands ran from `/home/bobdobbs/Projects/herdr-state-manager`, exit status 0.
The report was written after these runs; no executable code/tests changed afterward.

Focused:
```text
.venv/bin/python -m pytest -q tests/test_durable.py tests/test_durable_acceptance.py tests/test_governance.py --tb=short
........................................................................ [ 85%]
............                                                             [100%]
84 passed in 3.89s
```

Populated migration and repeat/settings checks:
```text
.venv/bin/python -m pytest -q tests/test_governance.py::test_migration_populated_0003 tests/test_durable.py::test_migration_0004_preserves_populated_rows tests/test_core.py::test_migration_repeat_and_sqlite_settings --tb=short
...                                                                      [100%]
3 passed in 0.16s
```

Full regression:
```text
.venv/bin/python -m pytest -q
........................................................................ [ 33%]
........................................................................ [ 66%]
.......................................................................  [100%]
215 passed in 15.52s
```

`git diff --check`: exit 0, no output.
`git diff --name-only -- migrations/versions`: exit 0, no output (0005 is new/untracked;
committed migrations unchanged).

Acceptance regressions explicitly cover strict per-failure input/default/zero/legacy
missing JSON, inherited vs looser local cap, same identity after new approvals and
both local/ancestor revisions, distinct failure-kind allowance plus aggregate cap,
pre-contract usage for both caps, concurrent same-failure last-slot authorization,
metadata persistence/read/audit/bounds/types/project denial/no implicit grant, scope
failure despite approved grant, all checkpoint action enums before/after terminal
state, actual terminal evidence recording, review prohibitions and missing completion
requirements. Existing tests cover aggregate ancestor races, exact evidence revision,
supersession, expiry/requester/action binding, terminal grant consumption, rejection,
idempotent replay, restart recovery and rollback after flushed approval consumption.

## Remaining limitations and review boundary

- Declared identity/evidence/reference metadata is trusted input, not external content
  verification. HSM never fetches artifacts or executes proposed actions.
- Canonicalization deliberately merges all failures of one fixed kind on one task;
  semantic reclassification into a different kind cannot be detected. Aggregate caps
  still constrain total authorized retries. Rejection cannot be reopened via this API.
- Detail histories are complete/unpaginated; collection pagination bounds parent rows,
  not nested histories. Budget checks currently scan authorized records inside the
  writer transaction; large-ledger optimization is outside this narrow checkpoint.
- Checkpoint next actions describe eligibility at creation time, not an evergreen grant
  or a fully bound future action payload; every actual mutation revalidates authority.
- Required deliverables are local task obligations; parent/child lifecycle does not
  cascade. Stop-on-satisfaction applies only to HSM-mediated work, not live processes.
- Governed reparenting retains B's conservative denial. Runtime execution, external
  availability and noncooperating clients are outside enforcement.
- No new CLI/TUI controls, pause/resume, aggregate recovery or metrics are claimed;
  later checkpoints own those. No skill installation or live end-to-end deployment
  verification was performed.

Parent acceptance: full suite independently passed **215 tests in 15.60s**.
`git diff --check` passed. A fresh populated revision-0003 fixture upgraded through
0004 to final 0005, preserving original records in projects, tasks, sessions, events,
incidents and approvals. Repeated upgrade, foreign-key and integrity checks passed.
The parent reviewed stable per-failure accounting, metadata authority boundaries
and checkpoint action coverage. No deployment authorization is implied.
