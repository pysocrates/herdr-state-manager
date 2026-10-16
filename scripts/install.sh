#!/usr/bin/env bash
# Stage a production installation. Does not start services or overwrite databases.
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then
  echo 'Run as root after reviewing docs/OPERATIONS.md.' >&2
  exit 1
fi
if [[ ! -f /opt/hsm/pyproject.toml ]]; then
  echo 'Clone this repository to /opt/hsm first.' >&2
  exit 1
fi
if ! id hsm >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/hsm --shell /usr/sbin/nologin hsm
fi
install -d -m 0700 -o hsm -g hsm /var/lib/hsm
install -d -m 0750 -o root -g hsm /etc/hsm
python3 -m venv /opt/hsm/.venv
/opt/hsm/.venv/bin/python -m pip install /opt/hsm
for unit in hsm.service hsm-reconcile.service hsm-reconcile.timer; do
  install -m 0644 "/opt/hsm/deploy/systemd/$unit" /etc/systemd/system/
done
if [[ ! -e /etc/hsm/hsm.env ]]; then
  install -m 0640 -o root -g hsm /opt/hsm/.env.example /etc/hsm/hsm.env
fi
echo 'Staged only. Provision credentials, initialize schema, then enable services as documented.'
