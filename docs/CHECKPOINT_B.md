# Checkpoint B — Phases 1 and 2

Status: **COMPLETE — parent reviewed and independently verified.**
Source base: checkpoint A commit `5288435`. No commit, deployment, service restart,
credential change or live database access was performed. All database execution was
against pytest temporary fixtures. No recursive delegation or arbitrary execution
feature was introduced.

## Implemented

- Operator-only, project/action-authorized full contract revision creation, current
  and immutable history read API. Explicit expected task/contract versions, reason,
  authenticated actor, timestamp and expected versions are persisted. Revision
  append and task metadata/version changes are atomic. Existing terminal rules stay.
- Strict bounded specifications, real zero limits and null unlimited semantics.
  Scope locks on existing create/PATCH/transition domain paths; exact declared
  resource-label and path-string subset matching, never filesystem/glob authority.
- Direct-child lifetime authorization ledger and ancestor-relative descendant depth
  gates inside existing BEGIN IMMEDIATE writer transactions. Amendments validate
  existing usage and descendants and do not reset counters. Concurrent final-slot
  creation, revision amendments and amendment-versus-delegation are exercised.
- Original delegation records with parent, applicable direct-parent revision plus
  all governing revisions, authenticated authorizer, reason and timestamp. Legacy
  reparenting adds history instead of rewriting original provenance. Absolute
  delegation_depth is separate from normalized subtree display depth.
- Narrow governance-denial savepoint handling: roll back operation changes, then
  commit sanitized audit plus idempotency response where applicable. Tests include
  an injected flushed partial mutation to prove rollback, replay deduplication,
  unauthorized no-audit behavior and no rejected payload in audit.
- Persisted elapsed authorization window, including restart verification, expiry
  gating and safe failed/cancelled exits. Contract amendments preserve first origin.
- Acceptance correction: bounded `prohibited_operations`, strict boolean
  `stop_when_satisfied` and `expansion_requires_authorization` now persist. Exact
  HSM create/PATCH/transition prohibitions apply through all contracted ancestors;
  unknown labels remain advisory and valid failed/cancelled exits remain available.
  No automatic expansion is authorized, including when the expansion flag is false.
- Every new task-associated event and durable denial snapshots original applicable
  contract revision IDs (all ancestors included). Amendment events identify the new
  revision; old events are never reinterpreted after amendments. Legacy contract JSON
  missing the new fields remains usable without migration or history rewriting.

## Exact changed/created files

All paths are relative to `/home/bobdobbs/Projects/herdr-state-manager`:

- `src/hsm/models.py` — contract and delegation models; declared task resources/index.
- `src/hsm/schemas.py` — strict contract inputs and task resource/spawn fields.
- `src/hsm/governance.py` — new focused policy helper called by Service mutations.
- `src/hsm/services.py` — existing mutation gates, savepoint denial handling,
  idempotency integration and provenance readback.
- `src/hsm/app.py` — authenticated contract routes, denial response and readiness.
- `src/hsm/hierarchy.py` — absolute delegation_depth.
- `migrations/versions/0004_task_contracts.py` — additive migration, down_revision 0003.
- `pyproject.toml` — explicit packaging of 0004 migration.
- `tests/test_governance.py` — 35 focused cases including parametrized validation.
- `tests/test_approval_observations.py` — only expected readiness revision 0003 → 0004.
- `docs/API.md` — endpoint/payload/error/replay and enforcement contract.
- `docs/DESIGN_CONTRACT.md` — exact policy semantics, trust boundaries, limitations.
- `docs/CHECKPOINT_B.md` — this report.

Migrations 0001–0003 were not edited. Revision 0004 adds task resource_labels with
empty-list default, parent index, task_contracts and delegations tables/indexes.
It backfills currently surviving parent links with `migration:unknown` authorizer,
not invented historical identity. Readiness and package manifest now require/include
0004. A populated 0003 root/child fixture is upgraded and foreign keys checked in
`test_migration_populated_0003`; the existing complete suite also exercises upgrades.

## Exact final verification

Working directory: `/home/bobdobbs/Projects/herdr-state-manager`.

```text
.venv/bin/python -m pytest -q tests/test_governance.py
35 passed in 1.11s

.venv/bin/python -m pytest -q
165 passed in 10.38s

git diff --check
(no output; exit 0)
```

Final focused and full runs passed. The acceptance correction's first focused run
returned 1 failed, 34 passed: the new historical-event test used the event UUID with
Session.get, but Event's primary key is its integer cursor. One narrow model lookup
confirmed this test-only error; the test now snapshots by cursor. No production
failure or out-of-scope fix was involved. No dependency installation or network fetch was required.
The package manifest was updated; building/installing a wheel was not performed.

This acceptance-correction pass modified only `src/hsm/schemas.py`,
`src/hsm/governance.py`, `src/hsm/services.py`, `tests/test_governance.py`,
`docs/API.md`, `docs/DESIGN_CONTRACT.md`, and `docs/CHECKPOINT_B.md`.
It introduced no migration, commit, deployment or checkpoint C implementation.
Parent-owned `docs/GOVERNANCE_REVIEW_NOTES.md` was not modified.

## Deliberate limitations / parent review points

1. **Governed reparenting is fail-closed, not implemented as a transfer operation.**
   Any changed parent touching source/destination governed ancestry or a contracted
   node anywhere in the moved subtree is denied, including detachment and operator
   requests. This prevents bypass while preserving entirely contract-free moves.
   A future explicit transfer protocol would need its own authorization/budget rules.
2. **Checkpoint C remains required:** retry_budget, stop_when_satisfied and required_deliverables are
   stored only. B does not account remediation retries or block completion for missing
   deliverables. Approval consumption remains separate and is not a B policy gate.
3. Declared resources are opaque exact-match labels. Natural-language objectives and
   spawn reasons are not semantically interpreted; external commands/files/resources
   and noncooperating agents are outside enforcement. No process execution or kill.
4. Authorization uses trusted host UTC wall time, not observed CPU/process time.
   Its persisted origin is never reset by revision/restart. An explicit amendment can
   extend/remove the task's own window but cannot bypass an expired ancestor.
5. No deletion/edit API exists for history; local DB administrators can still change
   tables. Prior to 0004, removed legacy links cannot be reconstructed by this
   migration. The lifetime baseline is surviving legacy links plus new attachments.
6. Contract history and per-task delegation history are complete/unpaginated. Existing
   unbounded hierarchy semantics remain; paginated governance history can be added
   later if operational scale warrants it. No TUI work or recovery schema redesign.
7. `docs/GOVERNANCE_REVIEW_NOTES.md` appeared as an untracked file during execution;
   this worker read it but did **not** create or modify it. It is not part of the B
   file ownership/deliverable list above. No other unexpected changes were observed.

## Parent acceptance

The parent reviewed the missing-field/event-attribution correction and independently
ran the full suite: **165 passed in 10.40s**. `git diff --check` passed. A separately
prepared populated 0003 fixture upgraded to 0004, repeat upgrade passed, original
columns in projects/tasks/sessions/events/incidents/approvals remained unchanged,
and SQLite integrity/foreign-key checks passed. No live database was accessed.

Additional parent-owned B files: `skills/herdr-state-manager/SKILL.md`,
`skills/herdr-state-manager/scripts/hsm_request.py`, `tests/test_skill.py`, and
`docs/GOVERNANCE_REVIEW_NOTES.md`. The helper supports contract GET but rejects
operator contract writes. Its two tests are included in the full suite above.

The documented conservative reparenting policy is accepted. Retry/deliverable/
stop-on-satisfaction enforcement remains an explicit dependency of C, not an
already delivered behavior. The overall engineering task is not complete.
