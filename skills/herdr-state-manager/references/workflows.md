# HSM agent workflows

Run examples from the installed skill folder, or use its absolute script path.
Configure HSM_API_URL and **agent** HSM_TOKEN_FILE through your private environment;
no secrets belong in the skill or command arguments. HSM need not be installed in the
agent's Python environment. The helper does not retry or execute commands in panes.
Responses are JSON envelopes: data, and pagination for list endpoints. Resource IDs
are internal UUIDs; project IDs are slugs; task_key is unique within a project.

## Recover, create, read back

```bash
python3 scripts/hsm_request.py GET /state
python3 scripts/hsm_request.py GET /state --query project=example
```

Replace `example` with an actual authorized project. Snapshot schema_version 1 includes
active_tasks with versions, agent_sessions, active_panes, related ended resources,
pending_approvals, outstanding_approvals, unresolved_incidents, adapter_health,
recent_events, warnings and truncation. Revision is an event cursor, not an ETag for
observation timestamps. Runtime unavailability does not imply task failure.

Check all task pages for a known key when needed:

```bash
python3 scripts/hsm_request.py GET /tasks --query project=example --query limit=200 --query offset=0
```

Continue while pagination.has_more, increasing offset by limit. Snapshots never cap
active tasks. A harmless demonstration only requires a queued task; no execution or
lifecycle transitions are implied. Choose the task key once per logical request and
keep the idempotency key and body for any retries. For example:

```bash
python3 scripts/hsm_request.py POST /tasks --idempotency-key demo-registration-001 --data-file - <<'JSON'
{"project_id":"example","task_key":"DEMO-1","title":"Demo task","objective":"Verify durable registration; no external actions."}
JSON
```

Replace the example keys for distinct operations. GET `/tasks/RETURNED_UUID` to verify
project, key, queued state, version and ownership. Successful registration is not
proof of task execution. Existing duplicate task_key returns 409; inspect that record
rather than silently creating a differently named task.

## Session identity and pane linkage

Only register a profile you actually know. This creates an HSM identity; external ID
is optional and must come from verified Hermes/runtime metadata if supplied:

```bash
python3 scripts/hsm_request.py POST /agent-sessions --data-file - <<'JSON'
{"project_id":"example","profile":"worker","external_session_id":"CONFIRMED_HERMES_ID"}
JSON
```

Omit external_session_id if unknown; do not literally send the example marker.
Record returned internal session UUID. List sessions first when recovering, and inspect
after ambiguous creation timeouts; this endpoint does not deduplicate retries.

Task ownership PATCH (replace TASK_UUID, SESSION_UUID and version with actual values):

```json
{"expected_version":1,"owner_session_id":"SESSION_UUID"}
```

Send using `PATCH /tasks/TASK_UUID --data-file FILE`. Re-read to confirm version and
owner. An unknown real profile leaves the task unowned until clarified.

Inspect panes with `GET /panes --query project=example` and paginate. Existing pane
registrations are reused, not recreated. Confirm the external Herdr pane ID before
PATCHing `/panes/INTERNAL_PANE_UUID`:

```json
{"task_id":"TASK_UUID","agent_session_id":"SESSION_UUID"}
```

Do not guess which pane belongs to you from activity or title. Pane assignments have
no expected_version field in v1; avoid changing another worker's assignment, and
verify the result. For a genuinely unregistered pane POST /panes with project_id,
external_pane_id, provider="herdr", optional task_id and agent_session_id. The external
pane key is unique within its project/provider. Heartbeat POST uses `{}` at
`/panes/UUID/heartbeat` or `/agent-sessions/UUID/heartbeat`; heartbeat is self-reporting,
not runtime proof. Runtime PATCH can record supported observed status but must not
manufacture telemetry or change task lifecycle.

## Lifecycle and conflicts

Read the latest task version, then POST `/tasks/UUID/transition` with a unique logical
idempotency key and `{"expected_version":CURRENT_VERSION,"state":"planning"}`.
General PATCH edits title/objective/parent/owner/Git metadata and requires
expected_version; it cannot set state. Paths are metadata, not permission to inspect Git.

| From | Allowed destinations |
|---|---|
| queued | planning, blocked, cancelled, failed |
| planning | running, awaiting_approval, blocked, failed, cancelled |
| running | review, awaiting_approval, blocked, failed, cancelled |
| awaiting_approval | planning, running, review, blocked, failed, cancelled |
| blocked | planning, running, review, awaiting_approval, failed, cancelled |
| review | running, awaiting_approval, blocked, completed, failed, cancelled |
| completed, failed, cancelled | none |

Same-state requests are 409; terminal tasks cannot be edited. Do not skip planning or
review merely to satisfy a client request. Approval rejection leaves the task unchanged;
the orchestrator decides whether to block, replan or cancel within its authorized work.
A new task can reference a terminal task through parent_id when follow-up is authorized.

401/403: verify agent environment and project/action grants with the operator; never
borrow operator tokens. 409: inspect latest record and the server's API contract;
payload/key mismatch, stale version and forbidden edges need different corrections.
422: fix invalid fields/key/query. 503/timeouts: retry only a known idempotent operation
with the original payload/key and a small bounded retry count, then report the block.
Keys expire after 24h; inspect before retrying older operations. On session/pane create
ambiguity inspect before retrying. On consume ambiguity never obtain a second grant
or run the action again based only on an HTTP retry.

## MCP alternative

If configured, use hsm_state_snapshot, hsm_task_create/get/transition,
hsm_session_register/heartbeat, hsm_pane_register/heartbeat and
hsm_approval_request/get. Pass request payloads as the tool's request object and
idempotency_key where required. The MVP MCP tools do not expose task PATCH, pane
PATCH, operator resolution or consumption; use the authenticated REST helper for
agent-side operations not exposed there. Do not assume a missing tool exists.
