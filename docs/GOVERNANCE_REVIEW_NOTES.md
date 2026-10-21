# Governance independent review checklist

Supplement to checkpoint A map, based on the completed read-only backend and
operator/recovery reviews. These are acceptance checks, not implemented features.
Implementation ownership remains with the current checkpoint worker; no parallel
backend changes are authorized by this checklist.

## B review

- Attempt bypasses through existing create, PATCH (objective, paths, reparent,
  detach), and transition services as well as REST. Service callers receive trusted
  principals but governance invariants must still reside in shared domain methods.
- Race contract amendment against delegation, not just two child creations.
- Check denials retain audit records without partial task mutations; current generic
  Problem handling rolls back writes. Verify replay does not allocate twice.
- Make historical and current lineage/revision distinction explicit.
- Existing app.run validates the response envelope after commit. Read-back/replay
  must remain sufficient if response validation fails; do not refactor unrelated
  transaction behavior merely because this pre-existing characteristic was found.
- Existing agents have project/action access, not owner-based ACLs. Ownership metadata
  must not be confused with authenticated authority.

## C review

- Existing test_rejection_and_terminal_task_consumption does not exercise consuming
  an already approved grant after the task becomes terminal. Add that precise test.
- Approval for a specific contract revision must not authorize an amended operation.
- If a grant authorizes an HSM mutation, consume it atomically with that mutation;
  failed mutation must leave it unconsumed. Generic standalone approval consumption
  cannot be an unrestricted scope override.
- Test rejection replay under changed client identifiers, exact operation identity,
  mutation races, and native observation/grant isolation.

## D/E/F review

- CLI generic tables omit approval-specific operation/outcome fields; governance
  views need readable dedicated fields rather than relying on generic rendering.
- TUI /state plus approval-history reads are separate snapshots; do not advertise
  cross-view atomic consistency that the server does not provide.
- Preserve bounded history pages, filter-race protection, and last-good stale views.
- Event cursor is not a runtime-observation ETag; use consistent as-of timestamp and
  database read boundary for recovery, and report telemetry provenance separately.
- Cumulative session token counters are not task runtime or current context usage.
- Preserve unconfigured/unavailable/missing adapter distinctions and terminal states.

## Independent upgrade fixture

Prepared a populated revision-0003 temporary database with project, root/child tasks,
owner session, event, incident and rejected approval, including a terminal root with
an active child. The independent harness compares all original column values after
upgrade, repeats upgrade, and checks foreign keys/integrity. This is supplemental
manual verification, not a substitute for committed migration tests.

Harness: /tmp/hsm-governance-upgrade-check.py
Fixture: /tmp/hsm-governance-upgrade-1lfl7mat/state.db
Preparation passed. Upgrade verification is pending implementation; no deployed DB
was opened or migrated.
