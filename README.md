# Herdr State Manager

HSM is a small, terminal-first state authority for agent development environments.
It records projects, task ownership, session/pane registrations, operator approvals,
runtime discrepancies and append-only audit history. A fresh orchestrator recovers
outstanding work from one consistent snapshot. HSM never plans work, launches agents,
executes commands in panes, edits source, or deploys software.

Python 3.12+; Flask, SQLAlchemy, SQLite WAL, Alembic, Pydantic, Typer/Rich and Textual.
MIT licensed. No hosted services or LLM dependencies.

## Development installation

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
# Optional: pip install -e '.[test,mcp]'
python scripts/provision.py "$HOME/.config/hsm/dev-credentials" --project example
export HSM_DATABASE_URL="sqlite:///$HOME/.local/state/hsm/state.db"
export HSM_CREDENTIALS_FILE="$HOME/.config/hsm/dev-credentials/credentials.json"
export HSM_TOKEN_FILE="$HOME/.config/hsm/dev-credentials/operator.token"
hsm init
hsm serve
```

In another shell, activate the environment and set the same HSM_TOKEN_FILE and
HSM_API_URL (default `http://127.0.0.1:8787`). Credentials are not loaded from `.env`
automatically. Export variables or use a systemd EnvironmentFile.

Register a project as the operator (replace the slug to match provisioned grants):

```bash
python - <<'PY'
from hsm.client import Client
print(Client().request('/projects', method='POST', data={'id':'example','name':'Example'}))
PY
hsm
hsm status --project example --json
hsm tasks
hsm agents
hsm panes
hsm approvals
hsm events
hsm watch
```

Use the agent token file in agent environments. Agent grants are explicit projects
and actions; the operator token is never distributed to agents. The provisioning
script refuses an existing destination, generates separate random tokens and never
prints them. The server reads credentials at startup; restart after rotation.

## Workflow

1. Orchestrator registers an agent session, creates tasks with owner references,
   and registers panes using actual runtime identifiers.
2. Task transitions require an expected version and idempotency key. General edits
   cannot change lifecycle state. Completed/failed/cancelled tasks remain terminal.
3. Cooperating executors request an expiring approval for an exact action, target
   and immutable artifact. Operators run `hsm approve UUID` or `hsm reject UUID`;
   these display the full request and require explicit confirmation.
4. The requester atomically consumes the grant immediately before execution. HSM
   does not execute the operation. Unrestricted tools can bypass this protocol.
5. A replacement orchestrator reads `GET /api/v1/state?project=example` or
   `hsm status --project example --json`; no conversation history is needed.

`hsm watch` shows cached state, task details, agent telemetry, panes, approvals,
events, incidents and adapter health. Tab/arrows navigate; Enter inspects; enter a
project filter and press Enter; `r` refreshes, `q` exits. It never polls runtimes.
Normal CLI commands work without a graphical desktop. JSON output uses the REST
envelope and stable schema version for recovery snapshots.

## Task governance

Tasks may opt into immutable, operator-authorized contracts. Legacy uncontracted
workflows remain valid. Shared server-side checks govern child creation, declared
resource labels, prohibited HSM operations, delegation depth, lifetime child/retry
budgets and elapsed authorization windows. Contract amendments retain original
revision history; governed reparenting is conservatively rejected.

Deliverables store opaque artifact references and revision-specific validation
metadata, not binaries. Failure does not erase completed outputs. Checkpoints retain
known-good evidence; exceptions reuse incidents and existing approvals without
implicitly authorizing remediation. Required outputs gate completion. HSM enforces
these rules only on mediated operations, not arbitrary shell or filesystem activity.

All-state task recovery and metrics are available through authenticated REST:

```text
GET /api/v1/tasks/UUID/recovery
GET /api/v1/tasks/UUID/metrics
GET /api/v1/governance/metrics?project=example
```

Existing `/state` responses retain their v1 keys and add independently versioned
`governance` data. Recovery is read-only, contains a consistent event cursor and
captured time, and never resumes tasks. Metrics use persisted history rather than
recent-event widgets. Missing runtime/context telemetry and historical provenance
remain explicitly unavailable; alerts are deterministic signals, not model-intent
judgments. See [API details](docs/API.md) and the
[companion workflow](skills/herdr-state-manager/SKILL.md).

## Governance inspection and intervention

```bash
hsm scope FEATURE-1 --project example --json
hsm checkpoint FEATURE-1 --project example --limit 50 --offset 0
hsm pause FEATURE-1 --project example --reason "Review scope violation" \
  --idempotency-key feature-1-pause-review
hsm resume FEATURE-1 --project example --reason "Review completed" \
  --idempotency-key feature-1-resume-reviewed --expected-version 8
```

Task UUIDs work without `--project`; human keys require it. `scope` displays the
server's task, applicable contracts, remaining budgets, deliverables, unresolved
exceptions and runtime-control capability. `checkpoint` inspects a bounded page of
persisted checkpoints, including exact evidence and recorded next action; it never
creates a checkpoint, invents an action, restores a runtime or resumes work.

Pause/resume require an explicitly configured **operator** with `tasks:write` and
project access. Clients never load or promote to an operator credential after denial.
Both commands fetch current task/version, display target/version/reason, and require
explicit confirmation (even with `--expected-version`). A supplied version must match
the preflight read. Every attempted POST is followed by an independent task GET,
including denied or ambiguous writes. Readback failures are explicitly unverified;
there are no automatic retries. Preserve the exact key/payload for an ambiguous retry
and reconcile current state first. `--json` emits the readback envelope on stdout;
confirmation and safety notices go to stderr.

In `hsm watch`, the existing Tasks table starts expanded. Space on the task table or
the Expand/collapse button toggles descendants without changing task state; selection
and collapsed branches persist across refreshes. Owner/lifecycle columns remain,
with local/inherited pause, remaining child budget, ready/required deliverables and
unresolved-exception counts appended. Missing legacy governance is **unknown**, not
zero or healthy; null remaining allowance is unlimited. Full budgets/contracts are
available via **Scope** in the existing details area. **Pause**/**Resume** fetch a
fresh version and open a reason/confirmation dialog; Cancel/Escape makes no write.
Refresh is deferred while reviewing an intervention so its displayed version stays
pinned. A concurrent server change is rejected, not overwritten. Contract violations
remain visible in Events and their exact payloads via Enter; this recent snapshot is
not a complete audit ledger (`hsm events` exposes explicit pagination).

Governance pause blocks mediated authorization; **runtime control is unsupported and
unacknowledged**. It does not stop processes. Resume only clears the local pause;
ancestor pause, contracts, approvals and budgets still apply. Existing bounded approval
history, tab navigation, stale-view preservation and filter-race guards are retained.
See [checkpoint D integration evidence](docs/CHECKPOINT_D.md) for backend and
CLI/Textual verification. No installation or service restart is implied.

## Configuration

See [.env.example](.env.example). Server credentials are a JSON array configured by
HSM_CREDENTIALS_FILE (preferred) or HSM_CREDENTIALS. Each record has identity, token,
role (`agent`/`operator`), projects, actions. Operator wildcards are supported;
agents require explicit grants. Actions: read, projects:write, tasks:write,
runtime:write, approvals:request, approvals:resolve, approvals:consume.

Client variables: HSM_API_URL, HSM_TOKEN_FILE or HSM_TOKEN. Remote endpoints require
HTTPS; use SSH port forwarding for plain localhost access. Redirects are not followed
with bearer tokens. There are no shell execution endpoints.

Runtime observation defaults to no-op. Opt into Herdr with HSM_RUNTIME_ADAPTER=herdr.
Hermes metadata observation is configured by the operator through project configuration
`hermes_metadata_database` pointing to an explicitly selected metadata database.
Read [integration documentation](docs/HERMES_INTEGRATION.md) before enabling it.

## Verification and operations

```bash
pytest -q
hsm health --json
hsm backup /a/new/private/backup.db
hsm reconcile --json
```

Tests use temporary databases and mocked runtime integrations; no live agents are
required. Reconciliation/init/backup are local administrative commands. All normal
inspection, operator decisions, TUI and MCP go through authenticated REST.

See [API](docs/API.md), [architecture/design](docs/ARCHITECTURE.md),
[operations](docs/OPERATIONS.md), and [design contract](docs/DESIGN_CONTRACT.md).
Production templates use a dedicated account, Gunicorn, and a five-minute systemd
timer. `scripts/install.sh` stages files only; service activation is manual.

## Limitations

- Installations are independent; no cross-machine replication or scheduling.
- Herdr adapter targets the locally verified 0.9.1/protocol 22 schema; unsupported
  protocols or inaccessible runtimes report unavailable. Live read-only observation
  was verified locally against two Hermes panes; runtime access remains host-specific.
- Hermes integration is a version-specific, opt-in, read-only metadata database
  adapter, not a dashboard API integration. History does not establish process
  liveness or context capacity. No compaction health estimate is manufactured.
- Correlation is registered explicitly and compared only against reported stable IDs.
- Events are retained indefinitely; no public mutation/deletion interfaces.
- Recovery includes all active resources in memory. Suitable for local development
  workloads, not arbitrarily large installations.
- Linux/systemd deployment templates are provided; production deployment and Ubuntu
  end-to-end service installation require operator validation on the target host.

## Companion Hermes skill

The portable [herdr-state-manager skill](skills/herdr-state-manager/SKILL.md) teaches
agents task registration, ownership/pane linkage, recovery, retries and approvals.
Its request helper uses only Python's standard library; agents do not need HSM installed.
To install into the current Hermes profile's skill directory, copy the folder without
overwriting an existing skill (adjust HERMES_HOME for a named profile):

```bash
HSM_SKILL_DEST="${HERMES_HOME:-$HOME/.hermes}/skills/herdr-state-manager"
test ! -e "$HSM_SKILL_DEST" && mkdir -p "$(dirname "$HSM_SKILL_DEST")" && cp -R skills/herdr-state-manager "$HSM_SKILL_DEST"
```

Set the agent's HSM_API_URL and HSM_TOKEN_FILE to a **scoped agent token**, separately
from the skill. Do not source an operator client environment into an agent. Load the
skill through Hermes skill discovery or explicitly preload it with
`hermes --skills herdr-state-manager`. Existing sessions may need to refresh their
skill list. Copying this folder does not provision credentials or register resources.

## Larger tasks and agent-owned subtasks

Keep project_id for the software project. Use parent_id to connect each agent's task
to the larger task; children can themselves have children. Every task has its own
owner_session_id, pane assignments, version and lifecycle. The orchestrator creates
and delegates work; HSM records that structure.

```bash
hsm tasks --project example --tree
hsm task FEATURE-1 --project example --tree
hsm tasks --project example --parent FEATURE-1
hsm subtask FEATURE-1 --project example --key FEATURE-1-API \
  --title "Implement API changes" --objective "Deliver the API part of FEATURE-1" \
  --idempotency-key feature-1-api-create
```

The parent must already exist. Add `--owner-session SESSION_UUID` when the actual
worker session is registered. Assign its confirmed pane using the REST API or bundled
skill helper. Use the same key/payload when retrying this specific creation; choose a
new key for a different subtask. CLI creation independently reads back the child.

Task lists show the parent key and owner profile. `--tree` includes completed/failed/
cancelled descendants; default status/watch views show active work with its ancestors
and progress counts that also include closed descendants. Watch's progress column is
completed/total descendants, not a percentage of engineering effort. Select a row to
inspect direct-child/descendant state counts, owner UUID and pane IDs.

Parent and child states remain independent. Completing children never auto-completes
the parent; parent completion/cancellation never cascades to children. Review and
complete the larger task explicitly. Terminal parents with active descendants produce
recovery warnings, including intentionally linked follow-up work. Legacy uncontracted
reparenting is an expected-version task PATCH and is audited; cross-project links and
cycles are rejected. Governed or effectively paused tree moves are denied rather than
silently transferring authority. The forest reuses `parent_id`; governance adds
forward migrations 0004–0006 without replacing that relationship.
