"""Verify the shipped standalone agent helper against the actual authenticated API."""
import json
import os
from pathlib import Path
import subprocess
import sys
from test_delivery import http_api

HELPER = Path(__file__).resolve().parents[1] / 'skills/herdr-state-manager/scripts/hsm_request.py'


def invoke(endpoint, *args, body=None, token_file=None):
    env = {**os.environ, 'HSM_API_URL': endpoint, 'HSM_TOKEN': 'a' * 32}
    env.pop('HSM_TOKEN_FILE', None)
    if token_file:
        env['HSM_TOKEN_FILE'] = str(token_file)
        env.pop('HSM_TOKEN', None)
    return subprocess.run([sys.executable, str(HELPER), *args], input=json.dumps(body) if body is not None else None,
                          text=True, capture_output=True, env=env, timeout=15)


def test_skill_helper_registration_readback_retry_and_scope(http_api, tmp_path):
    body = {'project_id': 'alpha', 'task_key': 'SKILL-1', 'title': 'Skill demo', 'objective': 'Registration only'}
    args = ('POST', '/tasks', '--data-file', '-', '--idempotency-key', 'skill-demo')
    first = invoke(http_api, *args, body=body)
    assert first.returncode == 0, first.stderr
    task = json.loads(first.stdout)['data']
    replay = invoke(http_api, *args, body=body)
    assert replay.returncode == 0 and json.loads(replay.stdout) == json.loads(first.stdout)
    assert task['state'] == 'queued' and task['owner_session_id'] is None
    private = tmp_path / 'agent.token'
    private.write_text('a' * 32)
    readback = invoke(http_api, 'GET', '/tasks/' + task['id'], token_file=private)
    assert readback.returncode == 0 and json.loads(readback.stdout)['data'] == task
    denied = invoke(http_api, 'GET', '/state', '--query', 'project=beta')
    assert denied.returncode == 1
    assert json.loads(denied.stderr)['error']['status'] == 403
    assert 'a' * 32 not in first.stdout + first.stderr + denied.stdout + denied.stderr


def test_skill_helper_rejects_operator_routes_missing_keys_and_remote_http():
    denied = invoke('http://127.0.0.1:1', 'POST', '/approvals/00000000-0000-0000-0000-000000000001/resolve', '--data-file', '-', body={})
    assert denied.returncode == 2 and 'Unsupported agent endpoint' in denied.stderr
    missing = invoke('http://127.0.0.1:1', 'POST', '/tasks', '--data-file', '-', body={})
    assert missing.returncode == 2 and 'requires --idempotency-key' in missing.stderr
    remote = invoke('http://example.com', 'GET', '/state')
    assert remote.returncode == 2 and 'require HTTPS' in remote.stderr
