# Checkpoint D — frontend handoff

Status: **pending integration**. Frontend implemented against the agreed REST
contract; backend-worker integration/authorization acceptance remains parent-owned.
No deployment, live-client requests, process control, commits or metrics work.

## Exact frontend-owned files

- `src/hsm/cli.py`: scope, recorded-checkpoint inspection, confirmed pause/resume.
- `src/hsm/tui.py`: existing task table hierarchy toggles/governance columns, scope
  details, version-pinned reason/confirmation dialog and independent REST readback.
- `tests/test_governance_ui.py`: isolated mocked HTTP transport exercising the real
  Client serialization/auth/error path, CLI runner and Textual `run_test`.
- `README.md`: commands, watch controls, authority/runtime and retry caveats.
- `skills/herdr-state-manager/SKILL.md`: companion recovery/inspection/operator guidance.
- `docs/CHECKPOINT_D_UI.md`: this handoff.

All other modified/untracked backend/schema/API/design/migration/test files in the
shared worktree belong to concurrent work; they were not edited by this frontend task.

## Contract and behavior

- GET `/tasks/UUID/scope` displays task, contracts, remaining_budget, deliverables,
  unresolved_exceptions, runtime_control without reinterpretation.
- GET `/checkpoints?task_id=UUID&limit=N&offset=N` reads one explicit page including
  full checkpoint records. No checkpoint creation or invented next action.
- POST `/tasks/UUID/pause|resume` sends exactly expected_version/reason and an
  Idempotency-Key. Existing configured credentials only; no implicit promotion.
- CLI requires reason/key, reads/displays task/version and confirms. An optional
  explicit version must match that read and never bypasses confirmation. JSON
  stdout remains a machine-readable readback envelope; prompts go to stderr.
- Watch reads the selected task before showing its identity/version and a reason
  dialog. Confirm alone writes; cancel/Escape does not. Its generated logical key
  is displayed. Refresh is deferred during confirmation and a concurrently changed
  server version is never silently refreshed/retried before writing.
- Every attempted intervention independently reads the task afterward, including
  denial/conflict; failed readback explicitly leaves the outcome unverified.
- Space (task-table focus) or the button collapses/expands existing hierarchy rows.
  Existing owner/progress columns and Enter/tab/navigation remain. Local/inherited
  pause, remaining children, ready/required deliverables and exceptions are appended.
  Legacy missing governance is unknown; null remaining children is unlimited.
- Scope and contract-violation event payloads use existing details; no tab redesign.
  Approval history remains a single bounded page, with failed-fetch preservation
  and in-flight filter/source guards. Scope additionally guards selected task/filter.

## Test coverage and execution

New tests cover CLI task key resolution, both interventions, required inputs,
confirmation/cancellation, explicit-version mismatch, HTTP 403/409, independent
readback versus an intentionally misleading POST response, failed readback,
parseable JSON stdout, scope and bounded checkpoint inspection. Textual tests cover
expand/collapse persistence, governance/owner columns, legacy fields, scope and
violation details, reason validation, confirmation/cancel for pause/resume, deferred
refresh, denial/stale version/readback failure, no retries, scope/history filter races.
The fake opener checks the unchanged scoped bearer token and exact REST payload/key.
No live API is used. Existing API-backed UI tests use their own temporary fixtures.

Initial targeted UI run: **19 passed** (before final JSON/readback guard additions).
A wider command initially named nonexistent `tests/test_cli.py`; no tests ran. One
narrow test-file search identified existing CLI coverage in core/hierarchy/delivery.
The corrected run found **2 failed, 129 passed**: Click's prompt/input echo polluted
JSON stdout. One narrow source inspection confirmed prompt handling; the frontend
now redirects the full confirmation interaction to stderr. Static analysis also
caught Textual's reserved `task` property before execution; the dialog uses
`task_record` instead. An ambiguous patch was rejected atomically and reapplied with
unique context; no partial writes occurred.

Final verification: **131 passed in 16.37s**, exit 0. `git diff --check` passed
with no output. This covers the final frontend implementation and tests; the only
subsequent edit records this result in this handoff document. Integration status
remains pending; this is not a full backend suite or live-service acceptance.

```sh
.venv/bin/python -m pytest -q tests/test_governance_ui.py tests/test_tui.py \
  tests/test_native_approval_tui.py tests/test_core.py tests/test_hierarchy.py \
  tests/test_delivery.py tests/test_skill.py
git diff --check
```

## Limitations / parent acceptance boundary

- D backend endpoints/authorization, durable audit semantics and live service behavior
  are not established by the mocked transport tests. Integrate with backend worker
  tests and full-suite acceptance before marking D complete.
- Runtime control is unsupported/unacknowledged. No processes stop/start; clearing a
  local pause does not clear ancestor governance or other policy constraints.
- Watch uses snapshot active-task hierarchy (including retained ancestors), not an
  unbounded historical tree. CLI task/tree/checkpoint commands inspect older tasks.
- Recent event payloads are bounded snapshot data, not a complete violation ledger.
  Use existing paginated events CLI/API for older audit evidence.
- Scope shows the server's complete projection; it does not compute metrics or invent
  budget/authorization semantics. No execution, checkpoint creation or recovery action.
- The companion helper allowlist is not expanded; scope uses CLI/direct REST.
- Skill edits are repository source only, not installation into any Hermes profile.
