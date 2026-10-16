# Hermes / Herdr integration

HSM records intent and facts. Astra/Hermes plan and delegate; Herdr owns panes; Git
owns repository content; the knowledge base owns verified project knowledge; the
human operator grants sensitive permissions. HSM has no spawn/prompt/kill/execute tool.

## Verified local discovery (2026-10-16)

Herdr `--version` reported 0.9.1. `api --help` advertises snapshot and schema;
`api schema --json` exported schema_version 1, protocol 22. SuccessResponse contains
result type session_snapshot and snapshot with protocol, version, panes. PaneInfo
requires pane_id, terminal_id, workspace_id, tab_id, focused, agent_status, revision.
AgentStatus supports idle/working/blocked/done/unknown. Optional agent_session has
source, agent, kind (id/path), value. The adapter preserves only operational fields;
it does not read pane terminal text, titles, cwd, or tokens dictionaries.

HerdrAdapter runs fixed argv `[herdr, api, snapshot]` with no shell, a five-second
timeout, suppressed stderr and a 4 MiB output read limit. It accepts a direct snapshot
or exported API envelopes, validates protocol 22, and reports unavailable for failures
or incompatible shapes. A local live trial successfully observed two Hermes panes
through this adapter. Verify runtime access on each deployment host. It never invokes mutating Herdr commands. A sufficiently
large runtime output is rejected; the temporary output file is not a hard disk quota.

Hermes `--version` reported Agent 0.19.0. Sessions list help has no structured-output
flag. Installed hermes_state.py SCHEMA_SQL confirms sessions columns id, profile_name,
model, started_at, ended_at and input/output/cache/reasoning token counters. No stable
dashboard metadata REST contract was found in the installed distribution; none is
invented. HermesAdapter opens an operator-selected database mode=ro, sets query_only,
checks column compatibility, and selects only these fields for registered external IDs.
It never instantiates Hermes SessionDB (which may migrate) or queries message/prompt
columns. It bounds SQLite execution and rejects oversized metadata. No live session
history was read during development or the pane-observation trial.

Configure project `configuration.hermes_metadata_database` at project creation to
opt in. Leave absent for unavailable/no-op. The operator must explicitly grant the
service account read/traverse access to that file and its SQLite WAL/SHM directory;
do not run HSM as root or open all private Hermes configuration. The systemd template's
ProtectHome hides home paths: use a narrowly reviewed drop-in for a dedicated metadata
location/access arrangement. Do not copy a live SQLite file without its proper backup.
Different Hermes profiles may have separate databases: one selected metadata database
per project is supported in v1. Schema drift reports unavailable.

A session history row is not proof of a live process: unfinished records are unknown,
ended records terminated. Token counters are observational totals, not context-fill
percentages, and no compaction health is inferred. Herdr correlation is compared only
when it reports Hermes kind=id matching a registered external_session_id. Mismatches
raise incidents; no assignment is automatically rewritten. Titles are never identity.
Unregistered runtime panes cannot safely be attributed to a project and are not imported.

## Orchestrator workflow

1. Retrieve an authorized state snapshot before planning after any context reset.
2. Register each known session/profile and its stable external ID if available.
3. Create project-local task keys, parent references and owner_session_id. Record
   worktree paths/base commits as metadata after external tools establish them.
4. Have Herdr perform pane creation outside HSM; register the returned pane ID with
   project, task and optional session. Send explicit heartbeats to HSM if supported.
5. Use expected_version and a fresh logical-operation idempotency key for transitions.
   On 409 re-read; do not blindly overwrite. Preserve keys across transient retries.
6. Request sensitive operation approval with exact immutable artifact, target and
   action. Operator reviews through CLI. A cooperating execution adapter consumes the
   matching single-use approval before executing. HSM never executes it. If the actual
   tool does not enforce consumption, HSM cannot prevent bypass.
7. Report review/completion explicitly. Neither idle/done telemetry nor approval
   resolution changes task lifecycle. Terminal follow-up requires a new linked task.

## Optional stdio MCP

Install `pip install '.[mcp]'`. Official Python MCP SDK FastMCP stdio is used.
Configure the client with an absolute environment-specific executable (do not commit
credentials to shared configuration):

```json
{"mcpServers":{"hsm":{"command":"/opt/hsm/.venv/bin/hsm","args":["mcp"],"env":{"HSM_API_URL":"http://127.0.0.1:8787","HSM_TOKEN_FILE":"/private/agent.token"}}}}
```

Tools: hsm_task_create/get/transition, hsm_pane_register/heartbeat,
hsm_session_register/heartbeat, hsm_approval_request/get, hsm_state_snapshot.
Tools call the authoritative authenticated REST service, reaching the same validation,
authorization and transactions as other clients. No operator resolution/consumption,
project administration, or execution tools are exposed. Stdio itself does not confer
permission. Separate agent token grants enforce project scope.
Manual administrative reconciliation reports a count of unregistered Herdr panes;
they are not attached to any project or exposed in project snapshots. Registered panes
without task assignments and panes attached to terminal tasks raise scoped incidents.

## Bundled agent skill

`skills/herdr-state-manager/` is a portable Hermes skill folder. Its SKILL.md routes to
focused registration/recovery and approval references. The stdlib-only request helper
supports authenticated agent operations without installing the HSM Python package;
operator resolution and project administration are deliberately outside its interface.
See README's companion skill section for copying into the intended Hermes profile.
Agent credentials are provisioned separately and never bundled with the skill.

## Decompose a larger task across workers

Register the larger task once in its actual project. When the orchestrator delegates,
create one child task per work item with parent_id=larger_task_UUID and an explicit
owner_session_id for the confirmed worker session. Nested decomposition is supported.
Register/link each real pane to the relevant child and session. Do not assign every
worker to the parent merely because all work belongs to one feature, and do not create
a new project identifier for each feature. HSM never spawns the requested workers.

Use hsm_task_tree(task_id) over MCP or GET /tasks/UUID/tree to inspect descendants,
owners, panes and state counts; hsm_task_create already accepts request.parent_id.
Re-read versions before lifecycle changes. Children report their own progress; the
orchestrator reviews the aggregate and transitions the parent explicitly. Failed or
cancelled children are not counted as completed. Parent completion/cancellation does
not stop workers or transition their tasks. Review remaining active descendants.
