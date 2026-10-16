# Native Hermes approval observations (optional)

These are **telemetry, not HSM authorization grants**. They cannot resolve, consume,
create or replace `/approvals`, and never transition tasks. Reported outcomes are
assertions by the authenticated reporter, not independently verified human consent.

## Correlation limitation verified against installed Hermes 0.19.0

`tools/approval.py` emits pre/post hooks with surface, command, description,
pattern keys and session_key; `_fire_approval_hook` additionally supplies turn_id
and tool_call_id. **It does not supply a native approval request ID**. A tool may
request more than one approval (including smart denial followed by an owner
prompt). Session, turn, tool, command hash and timing are not unique request IDs.
The plugin intentionally DOES NOT guess a pairing. Each callback gets an event
UUID (delivery identity, not native request identity). A pre stays pending with
`correlation=missing_request_id`; a post is a separate outcome with the same
missing-correlation flag. Pending means "request observed, no correlated outcome",
NOT proof the prompt is still live. Do not count these as outstanding HSM grants.
No native request identity is invented. Automatic pairing is blocked until Hermes
provides a verified stable request ID. Parent displays should clearly disclose this.

Source contracts inspected: installed `tools/approval.py`, `hermes_cli/plugins.py`,
and https://hermes-agent.nousresearch.com/docs/developer-guide/observer-hooks.
Only `ctx.register_hook` is used. Callbacks always return None; no veto/preanswer.

## REST contract

`POST /api/v1/approval-observations`, bearer credential with project grant and
`approvals:observe` action. This is a distinct grant from `approvals:request`.
Returns 200 `{data: observation}`. Required fields:

```json
{"project_id":"example","event_id":"7b9c2214-5f2a-4ddd-af05-a13ae16e2f30","phase":"post","surface":"cli","choice":"deny"}
```

`phase`: pre/post; `surface`: cli/gateway/smart. Post requires choice, pre forbids
choice except null. Choices: once/session/always/deny/timeout/smart_approve/smart_deny.
Optional `session_key_hash`, `command_hash`, `native_request_id` must be lowercase
64-hex SHA-256 digests. The last is ONLY a hash of a genuine stable upstream
request ID (namespace it upstream if IDs are not globally unique); the supplied
plugin leaves it absent. No arbitrary metadata, raw commands, notes or secrets.
Hashes can still permit dictionary attacks: the plugin omits command hashes.
Timestamps record HSM receipt time, not claimed native decision time.

Event IDs are durably deduplicated per authenticated identity, without the normal
24-hour idempotency expiration. Changed payload for the same event is 409. Retrying
an earlier event returns the current observation. Native request digests correlate
within project + authenticated reporter. Final choices and request metadata cannot
be overwritten. Post without pre is accepted (including after server restart);
a late pre can record request receipt but never regresses outcome. Missing native
IDs produce distinct uncorrelated records. There is no resolution/update endpoint.

`GET /api/v1/approval-observations?project=example&status=outcome&limit=50&offset=0`
requires `read`. Project and status are optional. Status pending/outcome; limit
1..200, offset >=0, stable UUID ordering and normal `pagination.has_more`.
Unauthorized projects are filtered or explicit project filters rejected. Writes to
disabled projects are rejected. Audit events and observations commit atomically.
Schema migration 0003 adds only observation/receipt tables; no legacy grant rewrite.

## Delivery and privacy

Plugin uses one daemon delivery thread, queue capped at 128 events, two-second HTTP
socket timeout and at most three attempts with identical event ID/payload. Full
queue, exit/crash, invalid payload or exhausted retries lose telemetry; this is
best-effort, NOT a durable spool. `Observer.dropped` counts drops; exhausted delivery
logs a generic warning with no body/token/exception. Shutdown does not block native
approval waiting for delivery. Callback does no network/disk I/O, stores no raw
command/description/patterns, and ignores arbitrary hook kwargs. Only bounded
session-key hashing occurs synchronously. HTTPS or loopback HTTP only, no redirects
(to avoid credential forwarding). Configure a trusted endpoint and restrict token
file to 0600. No plugin is installed/enabled by this repository automatically.

## Exact deployment sequence (operator only; NOT executed during development)

1. Stop the HSM service and back up its database with the existing `hsm backup`
   command; use your configured `HSM_DATABASE_URL`. Update/install this repository
   into the service virtualenv. Run `hsm init` using that same database URL to apply
   migration 0003, then restart the service. Never point a test invocation at live DB.
2. Add a dedicated credential record to the configured HSM credentials JSON, with a
   newly generated private >=32-character token, unique identity, role `agent`,
   explicit projects `["YOUR_PROJECT"]`, actions `["approvals:observe"]`. Add `read`
   only if this identity also needs to list observations. Restart HSM to reload
   credentials. Write the same token into a private 0600 file outside the repository.
3. For the **intended Hermes profile only**, run the following after replacing the
   explicit configuration values. `HERMES_HOME` must designate that profile, not a
   different active profile by accident:

   ```sh
   export HERMES_HOME=/absolute/path/to/intended/hermes/profile
   mkdir -p "$HERMES_HOME/plugins"
   cp -R /absolute/path/to/herdr-state-manager/deployment/hermes-approval-observer \
     "$HERMES_HOME/plugins/hsm-approval-observer"
   export HSM_OBSERVER_PROJECT=YOUR_PROJECT
   export HSM_OBSERVER_TOKEN_FILE=/absolute/private/observer.token
   export HSM_OBSERVER_URL=http://127.0.0.1:8787
   hermes plugins enable hsm-approval-observer
   ```

   Preserve those environment variables in the launcher/service of each opted-in
   Hermes process, or put the same keys/values in a private `settings.json` beside
   the installed plugin `__init__.py` (environment variables take precedence).
   This permits profile-local setup without global shell changes. Restart that
   process so plugin discovery and registration run.
   Use a separate project/reporter credential per project; do not use this fixed
   project configuration for a multi-project gateway. The plugin does not infer
   project or profile ownership from prompts or session keys.
4. Verify authenticated `/api/v1/health/ready` reports `schema_revision=0003`.
   Generate a normal approval during intended work (do not execute a dangerous test
   command just to test telemetry). With a read-authorized token inspect the GET
   route above. Expect separate missing-correlation pre/outcome records on 0.19.0.
   Verify HSM `/approvals` is unchanged. To disable: `hermes plugins disable
   hsm-approval-observer` in that same profile, then restart Hermes.

No live DB migration, native approvals, or live plugin enablement was performed by
the implementation tests. Fake hook API tests cover registered callbacks, privacy,
queue overflow, delivery retries, and actual REST ingestion in temporary databases.
