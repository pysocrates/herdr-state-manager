# Checkpoint D backend — implemented, focused verification passed

## Outcome and boundaries

Operator reversible governance pause is separate from lifecycle and applies through
uncontracted ancestry. Shared mutation gates enforce it; no runtime execution,
process control, recovery/metrics phase E, commit, live migration, deployment or
restart was performed. Runtime-control support and acknowledgement are explicitly
false. UI/client/README/skill-source work is owned by the parallel UI worker.

## Backend files

- `src/hsm/interventions.py` (new): operator intervention, pause ancestry, shared
  summary and authorized scope with per-ancestor authoritative budget constraints.
- `src/hsm/models.py`: persisted local pause and latest actor/time/reason.
- `src/hsm/schemas.py`: strict versioned bounded nonblank intervention payload.
- `src/hsm/services.py`: governance attached to the existing shared task forest,
  consequently detail/list/tree/state hierarchy views.
- `src/hsm/governance.py`: reparent source/destination/subtree checks and prohibition
  on paused contract amendments.
- `src/hsm/durable.py`: pause gate in shared check_work, inherited by child creation,
  PATCH/work transitions, grant consumption and remediation request/authorization.
- `src/hsm/app.py`: GET task scope, POST pause/resume, authenticated pre-replay
  permission checks, readiness revision 0006.
- `migrations/versions/0006_governance_pause.py` (new): additive forward migration,
  false default for legacy rows, nullable audit fields; no prior migration changes.
- `pyproject.toml`: package migration 0006.
- `tests/test_interventions.py` (new): seven test cases (including parametrization).
- `tests/test_approval_observations.py`, `tests/test_governance.py`,
  `tests/test_durable.py`: expected head/readiness revision updated to 0006.
- `tests/test_durable_acceptance.py`: existing full-view equality updated to expect
  the new authoritative unresolved-exception count; all original task fields still
  compared unchanged.
- `docs/API.md`, `docs/DESIGN_CONTRACT.md`: exact API and enforcement semantics.
- `docs/CHECKPOINT_D_BACKEND.md`: this report.

## Exact focused verification

Executed in `/home/bobdobbs/Projects/herdr-state-manager`:

```sh
.venv/bin/python -m pytest -q tests/test_interventions.py tests/test_governance.py tests/test_durable.py tests/test_durable_acceptance.py tests/test_approval_observations.py
```

Final result: **98 passed in 5.47s** (exit 0).
`git diff --check` also passed. Backend code/tests were unchanged after the passing
run; subsequent edits only documented the implemented behavior and this report.
Parent owns the combined full-suite run after both workers finish.

New tests exercise permissions/project isolation, strict payloads, missing keys,
replay/mismatch/stale/same-state conflicts, audit reasons/identity/time, uncontracted
ancestor inheritance, local resume under a paused ancestor, protected changes,
contract/reparent bypass attempts, descendant-containing subtree moves, preserved
owner/artifact identity, durable evidence and checkpoints while paused, safe
review/completion and failure/cancel, terminal intervention denial, generic grant
and remediation authorization denial without grant consumption, scope schema and
ancestor depth/window allowance, ordinary hierarchy summaries, explicit unsupported
runtime control, populated 0005 task preservation, repeat migration, SQLite foreign
key/integrity checks and head revision. Existing focused suites additionally check
populated 0003/0004 preservation and earlier contract/durable authorization behavior.

## Diagnosis and acceptance notes

Initial run: 93 passed, five failures. One narrow source/search diagnosis established
one new test incorrectly named the existing Event.timestamp field, three old tests
expected prior head 0005, and one existing full task-view equality legitimately
changed because recording an exception now increments the derived governance count.
Only those attributable test expectations were corrected; no assertions on existing
persisted task fields were dropped. Final run passed all 98.

Policy deliberately denies **all ordinary PATCH** while effectively paused,
including reassignment, and denies contract amendment until resumed. Review and
completion remain safe closure bookkeeping under existing contract/window/lifecycle
and required-deliverable checks. Durable recording does not authorize external work.
Local resume cannot remove inherited pause or restart terminal tasks. Scope budget
values are not evergreen execution grants; ancestor direct-child slots are reported
per owner, never wrongly applied to grandchildren. Runtime adapters were not changed
or called. A noncooperating process may continue to run.

Observed sibling worktree changes (`src/hsm/cli.py`, `src/hsm/tui.py`,
`tests/test_governance_ui.py`) were not edited by this worker.
