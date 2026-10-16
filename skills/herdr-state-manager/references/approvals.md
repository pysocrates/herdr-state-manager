# Operation-specific approval protocol

An HSM approval is a single-use grant for one exact operation, not blanket permission
and not execution. The actual execution adapter must enforce it. Unrestricted shell
or tools can bypass HSM; this skill does not create a shell interception framework.

When the requested workflow requires operator authorization, POST `/approvals` with
an Idempotency-Key and these fields:

```json
{"project_id":"example","task_id":"TASK_UUID","operation":"deploy","target":"staging","action":"deploy immutable release abc","artifact":"sha256:abc","expires_at":"A_FUTURE_UTC_TIMESTAMP"}
```

Use a real immutable fingerprint/revision, exact target/action, actual task/project,
and future expiry no more than seven days away. Never put secrets in action text.
Requesting identity comes from the bearer credential. GET `/approvals/RETURNED_UUID`
and verify the exact request. Keep its UUID, idempotency key and body for recovery.
A demo registration request alone does not authorize requesting a deployment.

Only the human operator resolves requests, normally through `hsm approve UUID` or
`hsm reject UUID`, which show exact details and require confirmation. Do not invoke
these commands, acquire operator credentials or resolve your own approval as an agent.
The bundled helper intentionally does not expose resolution. Waiting for approval
does not require a task transition unless it reflects the actual work state.

After resolution GET the approval. Rejected/expired decisions grant nothing. Approval
does not resume the task automatically. Only an already authorized cooperating
execution workflow consumes an approved request using the original requesting identity:

```json
{"expected_version":2,"task_id":"TASK_UUID","operation":"deploy","target":"staging","action":"deploy immutable release abc","artifact":"sha256:abc"}
```

POST to `/approvals/UUID/consume`, using the current approval version and matching
fields exactly. Consumption fails for an expired, rejected, stale, mismatched, already
consumed request, another requester, or terminal task. Successful consumption records
consumed_at/consumed_by and increments version. The HSM helper never runs the action.

Consumption is not idempotent. If the response is lost, GET and inspect consumption
state; do not retry consumption or repeat execution blindly. If prior execution cannot
be established, stop the sensitive action and report uncertainty to the operator.
Changed artifact/action/target requires a new approval for the new exact operation.
Do not treat a previously consumed record as a reusable authorization.
