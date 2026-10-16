# Operations

## Production-style Ubuntu installation

Use Python 3.12+ (Ubuntu 24.04 or a comparable modern Linux). Review scripts and units
before running. These are deployment instructions, not actions performed by Codex.

```bash
sudo apt-get install python3 python3-venv git
# Clone the eventual repository URL into /opt/hsm (source should be root-owned).
sudo bash /opt/hsm/scripts/install.sh
sudo python3 /opt/hsm/scripts/provision.py /etc/hsm/initial-credentials --project example
sudo install -m 0640 -o root -g hsm /etc/hsm/initial-credentials/credentials.json /etc/hsm/credentials.json
```

Keep `/etc/hsm/initial-credentials` root-only. Distribute only the appropriate token
file to a private operator/agent configuration directory, mode 0600; never expose the
operator token to agents. `/etc/hsm/hsm.env` has nonsecret paths and defaults from
.env.example. The API service reads only credentials.json; it does not need token
files. Ensure `/opt/hsm` and its virtualenv are readable/traversable by hsm, but only
administrators can modify source. The installer never initializes an existing DB or
starts/enables services. Initialize as the service account:

```bash
sudo -u hsm env HSM_DATABASE_URL=sqlite:////var/lib/hsm/state.db /opt/hsm/.venv/bin/hsm init
sudo systemctl daemon-reload
sudo systemctl enable --now hsm.service hsm-reconcile.timer
sudo systemctl status hsm.service hsm-reconcile.timer
journalctl -u hsm.service -u hsm-reconcile.service
```

Set HSM_TOKEN_FILE for your operator CLI, then register a project (README example).
Each installation has an independent local database. Mutable files live in /var/lib/hsm;
credentials/config in /etc/hsm; source and virtualenv in /opt/hsm. Service runs non-root
with UMask 0077, protected system/home, private temporary directory, and restricted
writable paths. Default binding is 127.0.0.1:8787. Unit binding is explicit; adjust its
ExecStart if changing port/host, rather than assuming HSM_HOST affects Gunicorn.
HSM_HOST/HSM_PORT affect only `hsm serve`, the development server.

Runtime timer uses OnUnitActiveSec=5min. Adjust timer with a systemd drop-in; there is
no misleading HSM_RECONCILE_INTERVAL variable. Units run no-op adapters by default.
Enable Herdr only after verifying its fixed read-only interface, executable PATH,
runtime socket permissions and session environment under the service account.
The dedicated account cannot inherently see a desktop user's Herdr socket or Hermes
metadata. Review narrow access arrangements; do not grant blanket home/config access.
See HERMES_INTEGRATION. No polling occurs in the TUI itself.

For remote CLI use SSH forwarding (`ssh -L 8787:127.0.0.1:8787 host`) or an HTTPS
authenticated reverse proxy with rate/request limits. Do not expose Gunicorn publicly.
CLI supports explicit HSM_API_URL and bearer credentials; refuses remote plain HTTP.

## Initialization, migrations and upgrades

`hsm init` applies Alembic upgrades without dropping or resetting an existing database.
Automatic API startup never runs migrations. Readiness verifies the expected revision;
liveness only says the process can answer. Keep schema/runtime versions together.

1. Stop the reconciliation timer, wait for its oneshot service, and stop the API.
2. Make a verified backup and record the installed source revision/dependency set.
3. Update source/virtualenv under administrative control.
4. Run `hsm init` as hsm against the intended database URL.
5. Start API, check `hsm health --json` and inspect a snapshot, then start timer.
6. On failure stop writers and restore the backed-up DB with the matching prior code.

Migrations are included in built wheels. Production should pin the tested dependency
resolution in its deployment process; pyproject uses compatible major ranges. This
repository does not promise a cross-platform dependency lock.

## Backups and restore

Run `hsm backup /private/new-backup.db` as the filesystem administrator with the
correct HSM_DATABASE_URL. It uses SQLite online backup, opens source read-only and
refuses to overwrite the destination. Protect backups like the live state: operation
descriptors and metadata may be sensitive. Test restoration periodically using a
separate database URL and an integrity/readiness check before replacing live state.

Restore only while API and reconciliation services are stopped and no clients write.
Archive the existing database **and** its -wal/-shm files together; never mix stale
sidecars with a restored database. Put the verified backup at the configured path,
owned hsm:hsm, mode 0600, apply needed migrations, then start services and inspect state.
Do not copy an actively changing SQLite main file as a backup.

## Logs, retention and maintenance

Application logs are JSON with safe method/route/status fields; no bodies, tokens,
query strings, database errors or environment dumps. Gunicorn/systemd operational
messages may be plain text. Configure journald retention. HSM_LOG_LEVEL controls
application log verbosity; tokens are still never logged.

Events are immutable through public APIs and retained indefinitely in v1. There is no
public deletion endpoint. Plan disk capacity and back up/rotate offline archives.
Administrative deletion is an explicit offline maintenance operation after backup,
with all writers stopped, project-scoped selection reviewed and recovery impact
accepted. There is no automatic event pruning. Idempotency keys remain valid 24h;
expired rows are replaced on reuse (no background bulk pruning yet). Do not delete
active idempotency records or consumed approval records to reclaim space.

## Common failures

- 401: client/server token mismatch, unexported variables, or credential rotation.
- 403: missing project/action grant; approval resolution additionally needs operator.
- 409: stale version, invalid edge, conflicting key/payload, used/expired approval.
  Re-read state. Do not automatically bypass the conflict.
- 503: missing/mismatched migrations, busy SQLite after five seconds, or storage issue.
  Check service logs and correct database URL/permissions; never reset the database.
- Runtime unavailable: verify adapter configuration, schema version and service-account
  access. HSM remains functional; unavailable is never evidence of task failure.
- Stale heartbeat: only an incident. Inspect manually; never assume engineering failure.
- Overlapping reconcile: nonblocking flock refuses another process, including manual
  invocation concurrent with the timer. Keep lock file in the same trusted local state
  directory; don't remove an active lock file.

No services were deployed or enabled during implementation. Templates were statically
checked; full Ubuntu systemd installation requires validation on the destination host.
