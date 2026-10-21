# Verification

## Governance release acceptance (checkpoints A–F)

Checkpoint reports contain exact file inventories, scoped failures/diagnoses and
executed evidence: [B](CHECKPOINT_B.md), [C](CHECKPOINT_C.md),
[D](CHECKPOINT_D.md), [E](CHECKPOINT_E.md), [F](CHECKPOINT_F.md).
The F report is authoritative for final acceptance status and final test counts.
Worker handoffs marked pending are historical; use the parent checkpoint report.

### Automated coverage

- Existing complete lifecycle matrix, terminal immutability, explicit parent/child
  ownership, project permissions, optimistic task versions and idempotency behavior.
- Immutable contracts and event revision attribution, explicit operator amendments,
  concurrent delegation/amendment races, inherited scopes/depth/windows and limits,
  denied-mutation rollback with durable sanitized evidence.
- Deliverable revisions and exact evidence identity, fresh validation after updates,
  explicit supersession, terminal-failure durability, immutable checkpoints.
- Existing-approval remediation, requester/grant/revision binding, canonical rejection,
  per-failure and aggregate lifetime retry limits, atomic final-slot races.
- Operator pause/resume, inherited containment and reparent bypass rejection,
  independent CLI readback, real authenticated backend/Textual control integration.
- All-state recovery, separate-process restart, session replacement, current proposal
  eligibility, concurrent snapshot writes and complete-table comparison for read purity.
- Full-history metrics beyond recent-event caps, replay exclusion, historical lineage
  provenance, deterministic alert thresholds and unknown telemetry.
- Existing real two-worker Gunicorn HTTP and optional MCP stdio integration; runtime
  adapters use temporary/mocked resources, not live agents or external infrastructure.
- Populated migration preservation from prior schemas, repeated upgrade, foreign-key
  and SQLite integrity checks, WAL/busy-timeout behavior and readiness revision 0006.

### Distribution checks

Build source archive and wheel with `uv build`; inspect that migrations 0001–0006
are in the wheel and source archive includes the test fixtures, documentation,
companion skill and deployment templates. Install the wheel into a separate virtual
environment, run outside the checkout, and assert imports come from site-packages.
Exercise freshly/repeatedly migrated temporary state through authenticated readiness,
contracts, pause, recovery, metrics and state reads. No production database is needed.

The source manifest explicitly includes `tests/*.py`, including `conftest.py`, so
release archives retain their shared fixtures rather than only automatically selected
test modules. The companion skill ships as source in the archive; it is not silently
installed into Hermes or made a core runtime dependency.

### Reproducible repository checks

```bash
.venv/bin/python -m pytest -q
uv build --out-dir /tmp/hsm-release-dist
bash -n scripts/install.sh
git diff --check
```

Production upgrades remain an explicit operator operation. Passing tests does not
install or restart services, migrate deployed state, rotate credentials or prove
live adapter availability. Pause does not stop processes. Metadata validation does
not prove external artifact availability. No large-history performance or context
utilization estimate is claimed.

## Historical baseline

Before the governance work, checkpoint A independently verified 130 passing tests.
Earlier 2026-10-16 verification recorded 119 tests and wheel schema 0002; those counts
and schema claims describe that older tree, not the current release. Live/runtime
trials recorded in older documentation are not renewed by these automated checks.
