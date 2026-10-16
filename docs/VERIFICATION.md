# Implementation verification — 2026-10-16

119 tests passed (Python 3.13.14), including:

- Complete 9-by-9 state transition matrix and terminal immutability.
- Multi-agent parent/child trees, nested progress, project-scoped tree reads,
  versioned/audited reparenting, terminal ancestor recovery and CLI subtask creation.
- Watch hierarchy columns show task depth, parent, owner and descendant completion.
- Concurrent task writers, approval decisions and single-use consumption races.
- Idempotent retries, payload conflicts, revocation checks and no extra events.
- Cross-project authorization and parent/owner/session/pane relationships.
- Restart recovery, uncapped active tasks, event truncation and consistent reads
  while another connection writes.
- Missing/unavailable runtime distinction, incident deduplication/resolution,
  nonoverlapping reconciliation and metadata-only Hermes adapter fixtures.
- Real two-worker Gunicorn server on temporary localhost socket, REST/CLI recovery,
  exact approval workflow, real optional MCP stdio exchange.
- Headless Textual navigation, cached-data retention during API outages and exit.
- Online backup, refusal to overwrite, schema migration repeatability and SQLite
  WAL/foreign-key/busy-timeout settings.

Wheel and source distribution build. Fresh wheel installation into an independent
virtualenv initializes revision 0002 and passes readiness and recovery outside the
source checkout. Migrations are packaged with the wheel; deployment scripts/docs are
included in the source distribution. Shell installer syntax checked.

Static systemd validation reports the expected absent /opt/hsm executables, with no
unit syntax errors. No services installed, enabled or started. Ubuntu production
installation has not been performed. Live Hermes conversation histories and Herdr terminal output were not read. A local
read-only trial verified two Hermes panes through the Herdr adapter; both initially
reported idle and exposed no stable Hermes session references. Automated runtime
tests use mocks. The bundled skill helper additionally passed creation/read-back,
idempotent replay, project denial and agent-route restriction tests.

This execution sandbox hangs during asyncio executor shutdown even in a minimal
standalone example. Headless TUI, real localhost and stdio tests pass outside that
restriction. Tests use only temporary databases and simulated resources.

Live runtime databases, credentials and trial configuration are outside the repository
and excluded from publication. No pre-existing project state database was overwritten.
Publication status is reported separately after remote read-back.
