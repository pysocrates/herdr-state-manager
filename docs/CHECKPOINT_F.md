# Checkpoint F — integration, compatibility and release verification

Status: **COMPLETE — parent verified and accepted.**
Base: checkpoint E `d5ae9f8`. Local acceptance only; no deployment or push authorized.

## Changes and exact files

- `MANIFEST.in`: explicitly include Python tests and shared `conftest.py` in the
  source archive. Initial build exposed test modules without their shared fixtures;
  one scoped manifest inspection established the cause.
- `src/hsm/app.py`: recheck reference-read permission before cached durable-write
  replay, only for operations/payloads that already require it on first execution.
- `tests/test_durable_replay_permissions.py`: eight grant-revocation combinations
  covering evidence, supersession and checkpoint references/actions, plus write-only
  checkpoint compatibility. Denied replay neither mutates data nor adds events;
  restoring permission permits the original cached response without reexecution.
- `tests/test_governance_workflow.py`: integrated authenticated temporary-state
  contract/delegation/durable-evidence/exception/approval/pause/recovery/failure and
  separate successful closure workflow, with authority and replay assertions.
- `README.md`: current governance/recovery/metrics overview, remove stale D pending
  integration claim, distinguish legacy reparenting from governed restrictions.
- `docs/ARCHITECTURE.md`: shared governance modules, additive migrations, savepoint
  denials, immutable evidence, consistent reads and runtime boundary.
- `docs/GOVERNANCE_IMPLEMENTATION_MAP.md`: mark original plan/baseline as historical.
- `docs/VERIFICATION.md`: current coverage, distribution checks and reproducible commands.
- `docs/CHECKPOINT_F.md`: this report.

## Independent review and correction

Read-only reviewer found one confirmed P2 authorization-boundary defect: cached
reference-reading durable-write responses could bypass revocation of `read`, while
fresh requests required it. Impact was cached-response disclosure/current-grant
inconsistency, not a repeated mutation or new execution authorization. Parent source
inspection confirmed evidence, supersession and relevant checkpoints needed the
pre-replay check. No new read requirement is imposed on unrelated write-only paths.
The regression covers revocation of both required grants separately, restored-grant
replay, unchanged audit history, and the non-reading checkpoint compatibility case.
Reviewer reported no other concrete governance bypass; this is scoped review, not
an assertion of exhaustive security proof. Integration worker changed only its
assigned new test. Parent owns the correction and acceptance.

## Migration and compatibility

None in F. Frozen chain remains 0001–0006; baseline migrations 0001–0003 have no
Git diff from the original baseline. No deployed databases were accessed.
Legacy uncontracted tasks, existing routes/response envelopes, explicit project grants,
optional observers and terminal semantics remain covered. `/state` adds versioned
governance fields without dropping v1 fields. Core has no new runtime/model dependency.

## Verification

Commands ran in the repository unless otherwise noted:

- New correction and integrated workflow:
  `.venv/bin/python -m pytest -q tests/test_durable_replay_permissions.py tests/test_governance_workflow.py`
  — **10 passed in 0.88s**.
- Final executable repository tree: `.venv/bin/python -m pytest -q`
  — **274 passed in 29.55s**. This includes real two-worker HTTP, optional MCP,
  headless Textual, races, recovery, pure reads and migration checks.
- Explicit populated 0003/0004 preservation and repeat/settings tests before the
  permission-only correction — **3 passed in 0.16s**; also included in final full run.
- `bash -n scripts/install.sh`: exit 0.
- Earlier wheel smoke outside the checkout passed site-packages import, packaged
  fresh/repeated 0006 migrations, authenticated contracts/pause/recovery/metrics/state,
  foreign-key and integrity checks. Extracted-source integration/recovery/metrics
  against independent fresh dependency resolution: **23 passed in 3.41s**.
- Source manifest correction verified by archive assertion: shared fixture/skill
  present, all six migrations in wheel. After the permission correction, rebuilt
  and reinstalled the wheel into the independent environment; repeated smoke PASS.
- Final extracted-source permission/workflow/CLI/Textual/recovery/metrics tests against
  that rebuilt wheel from `/tmp`: **33 passed in 4.00s**. No executable code changed
  afterward; only this acceptance report was updated.
- Artifacts: `/tmp/hsm-checkpoint-f-dist/herdr_state_manager-0.1.0-py3-none-any.whl`
  and `/tmp/hsm-checkpoint-f-dist/herdr_state_manager-0.1.0.tar.gz`.
  Test environment: `/tmp/hsm-checkpoint-f-wheel-env`.
  Smoke script: `/tmp/hsm-checkpoint-f-wheel-smoke.py`.

## Acceptance matrix and remaining limitations

| Requirement | Evidence / explicit boundary |
|---|---|
| Versioned enforceable contracts | B tests and current full suite; explicit operator amendments; null limits documented |
| Authoritative lineage and budgets | Serialized final-slot races, historical attribution; governed reparent transfers denied |
| Durable outputs | C/F workflows; immutable evidence revisions survive terminal task/session failure |
| Exception governance | Existing exact-action approvals, canonical rejection and aggregate/per-failure caps |
| Operator awareness/intervention | D CLI and Textual real-backend tests; process control unsupported/unacknowledged |
| Context continuity | E restart/session/concurrency/pure-read tests; stale proposal is null, never automatic resume |
| Behavioral signals | Full persisted history and deterministic thresholds; missing telemetry not estimated |
| Compatibility/migrations | Legacy suites, populated upgrades, packaged migration chain, fresh-wheel checks |
| Documentation/skill | API/design and companion source updated across B–E; F README/architecture/verification aligned |

HSM governs mediated state and authorization, not arbitrary tools or runtime execution.
Artifact references/evidence are metadata, not externally verified content. Canonical
failure categories merge occurrences; repeat-occurrence telemetry remains unavailable.
Missing historical lineage is not inferred from today's tree. Reads scale with retained
history; no large-installation performance claim. Optional runtime adapters remain
observational and expose no process control. Live deployment, service restart, installed
skill updates, pushes and target-host validation remain operator-controlled and were
not performed. No additional optimization or unrelated refactoring is part of F.
