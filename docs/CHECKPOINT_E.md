# Checkpoint E — COMPLETE

Scope: phases 6 recovery and 7 deterministic governance reporting only. Base supplied
by parent: checkpoint D `4ae889e`. Independently reviewed and accepted by parent.

## Delivered

- Authenticated all-state `/tasks/UUID/recovery`, exact-task `/tasks/UUID/metrics`,
  and project-scoped `/governance/metrics` GETs through existing read transactions.
- Additive independently versioned governance v1 section in outer `/state` v1,
  preserving every legacy key and active-with-ancestors selection. Full-history
  metrics include terminal tasks even when active recovery omits them.
- Exact contract/objective/constraint/budget projections, durable artifacts/evidence,
  checkpoints, unresolved exceptions, pause/lifecycle/ownership, historical successful
  authorization and conservatively revalidated recorded checkpoint next action.
- One captured time for all governance windows, transaction-consistent event cursor,
  no writes/resume/adapter calls during recovery or reporting.
- Whole-history counters and fixed inclusive alerts; separately labelled historical
  root-at-creation and current-root projections, unknown provenance/recurrence/runtime
  telemetry explicit. New audit request classes enable reliable denial attribution;
  old immutable events are unchanged and ambiguous legacy denials remain unclassified.
- Companion helper GET allowlist and authenticated integration coverage; no optional
  CLI commands added (existing clients can use the new REST reads).

## Exact files

New:
- `src/hsm/reporting.py`
- `tests/test_recovery_governance.py`
- `tests/test_governance_metrics.py`
- `docs/CHECKPOINT_E.md`

Modified:
- `src/hsm/services.py`
- `src/hsm/responses.py`
- `src/hsm/app.py`
- `src/hsm/interventions.py`
- `tests/test_skill.py`
- `tests/test_core.py` (one restart comparison uses the same captured read time)
- `tests/test_governance.py` (two exact denial-detail expectations include the new enum)
- `docs/API.md`
- `docs/DESIGN_CONTRACT.md`
- `skills/herdr-state-manager/SKILL.md`
- `skills/herdr-state-manager/scripts/hsm_request.py`

## Migration

**None.** Existing revision **0006** supplies the sources. No models, migrations,
readiness or packaging metadata changed. No deployed database was opened/migrated.

## Verification

Initial focused run before final coverage refinements:
`.venv/bin/python -m pytest -q tests/test_recovery_governance.py tests/test_governance_metrics.py tests/test_skill.py`
— **23 passed in 3.96s**.

Final verification of executable code:

- Focused E/helper suite after additional snapshot concurrency coverage:
  **24 passed in 3.68s**.
- First full run: **5 failed, 259 passed in 28.63s**. One scoped diagnostic found
  only directly attributable legacy assertion changes: nested governance timestamps
  differ across restart and sanitized denial details now contain request_type.
  Updated the one restart test to compare the same captured time and the two exact
  denial expectations (one parametrized), preserving every original field/security
  assertion. These two existing test files are the sole additions beyond the initially
  assigned file list; included explicitly above for parent review.
- Focused E/helper plus affected legacy cases:
  `.venv/bin/python -m pytest -q tests/test_core.py::test_context_recovery_restart_and_runtime tests/test_governance.py::test_denial_is_durable_sanitized_and_replayed tests/test_governance.py::test_prohibited_operations_are_atomic_and_inherited tests/test_recovery_governance.py tests/test_governance_metrics.py tests/test_skill.py`
  — **30 passed in 4.03s**.
- `.venv/bin/python -m pytest -q` — **264 passed in 28.45s**.
- Initial `git diff --check` found one added blank line at API.md EOF; removed in
  the documentation-only handoff update. Final `git diff --check` **passed**.

No executable code changed after the final passing suite. Existing full-suite tests
also exercise packaged migrations through 0006; E adds no schema changes and did not
run migrations against any live database.

Coverage includes all lifecycle states, real subprocess restart against temporary
state, session replacement, paused/terminal durable evidence, stale checkpoint
versions/contracts/evidence/windows/caller grants/output readiness, null intent,
cross-project auth, explicit interleaved concurrent commits, captured time/cursor,
full-table before/after pure-read comparison, >200 later events, replay exclusion,
historical reparent provenance and missing legacy ancestry, fixed alert boundaries,
unknown telemetry, readiness versus completion, retry accounting and helper routes.

## Remaining limitations / parent review

- Reads scale with retained task/history volume and reuse existing per-task/forest
  projections (not optimized materialized analytics). No large-scale performance claim.
- Amendment request counts mean successful revisions plus persisted policy denials;
  authentication, validation and stale-version failures were never recorded. Legacy
  ambiguous denials remain explicitly unclassified rather than guessed from a code.
- Canonical exception storage does not record duplicate occurrences. Recurrence is
  unavailable; the exception alert reports multiple canonical kinds, not repeated
  observed occurrences. Inspections are counted separately, never treated as failures.
- Historical roots are derived only where recorded parent history supports them;
  absent history stays unavailable, separate from current tree counts.
- Next action is a conservative recorded proposal, not a bound payload or durable grant.
  Paused/terminal recovery suppresses it even if explicit safe bookkeeping remains legal.
- Registered runtime status/heartbeat are source-labelled observations, not liveness;
  execution time/context utilization remain unavailable. No external adapter calls.
- Parent independently reviewed reporting, read-transaction integration, schema
  compatibility and audit request attribution; full suite passed **264 tests in
  28.66s** and `git diff --check` passed. Only this report changed afterward.
- No push, deployment, live service restart, live migration, installed-skill
  modification or recursive delegation. Restart coverage used only a separate test
  interpreter and temporary fixture database.
- Work pauses after checkpoint E at the user's request. Checkpoint F is unstarted;
  final integration/compatibility/documentation acceptance is not claimed here.
