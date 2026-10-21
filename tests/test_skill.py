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
    contract = invoke(http_api, 'GET', '/tasks/' + task['id'] + '/contract', token_file=private)
    assert contract.returncode == 0, contract.stderr
    assert json.loads(contract.stdout)['data'] == {'current': None, 'history': []}
    for suffix in ['scope', 'recovery', 'metrics']:
        result = invoke(http_api, 'GET', '/tasks/' + task['id'] + '/' + suffix)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)['data']
        if suffix != 'scope':
            assert data['schema_version'] == 1 and data['event_cursor'] > 0
    result = invoke(http_api, 'GET', '/governance/metrics', '--query', 'project=alpha')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['data']['counts']['tasks'] == 1
    result = invoke(http_api, 'GET', '/governance/metrics', '--query', 'project=beta')
    assert result.returncode == 1 and json.loads(result.stderr)['error']['status'] == 403
    denied = invoke(http_api, 'GET', '/state', '--query', 'project=beta')
    assert denied.returncode == 1
    assert json.loads(denied.stderr)['error']['status'] == 403
    child_result = invoke(http_api, 'POST', '/tasks', '--data-file', '-', '--idempotency-key', 'skill-child',
                          body={**body, 'task_key': 'SKILL-1-CHILD', 'parent_id': task['id']})
    assert child_result.returncode == 0, child_result.stderr
    tree = invoke(http_api, 'GET', '/tasks/' + task['id'] + '/tree')
    assert tree.returncode == 0, tree.stderr
    assert len(json.loads(tree.stdout)['data']['nodes']) == 2
    assert 'a' * 32 not in first.stdout + first.stderr + denied.stdout + denied.stderr


def test_skill_durable_endpoints_and_admin_isolation(http_api):
    def write(path, data, key):
        result = invoke(http_api, 'POST', path, '--data-file', '-', '--idempotency-key', key, body=data)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)['data']
    task = write('/tasks', {'project_id': 'alpha', 'task_key': 'durable-skill', 'title': 'Output', 'objective': 'Record'}, 'task')
    item = write('/tasks/' + task['id'] + '/deliverables', {'expected_version': 1, 'key': 'result',
        'title': 'Result', 'reference': 'opaque:file', 'artifact_identity': 'sha256:one'}, 'artifact')
    write('/deliverables/' + item['id'] + '/evidence', {'expected_version': 1, 'revision_id': item['revisions'][0]['id'],
        'artifact_identity': 'sha256:one', 'reference': 'opaque:log', 'outcome': 'passed'}, 'evidence')
    checkpoint = write('/tasks/' + task['id'] + '/checkpoints', {'expected_version': 1,
        'revision_ids': [item['revisions'][0]['id']]}, 'checkpoint')
    exc = write('/tasks/' + task['id'] + '/exceptions', {'expected_version': 1,
        'failure_kind': 'execution', 'description': 'failed attempt'}, 'exception')
    write('/exceptions/' + exc['id'] + '/inspect', {'expected_version': 1}, 'inspect')
    write('/exceptions/' + exc['id'] + '/resolve', {'expected_version': 2, 'note': 'recorded resolution'}, 'resolve')
    for path in ['/deliverables/' + item['id'], '/deliverable-revisions/' + item['revisions'][0]['id'],
                 '/checkpoints/' + checkpoint['id'], '/exceptions/' + exc['id'], '/validation-evidence', '/remediations']:
        result = invoke(http_api, 'GET', path)
        assert result.returncode == 0, result.stderr
    for suffix in ['contract', 'pause', 'resume']:
        result = invoke(http_api, 'POST', '/tasks/' + task['id'] + '/' + suffix, '--data-file', '-', body={})
        assert result.returncode == 2 and 'Unsupported agent endpoint' in result.stderr
    missing = invoke(http_api, 'POST', '/exceptions/' + exc['id'] + '/inspect', '--data-file', '-', body={})
    assert missing.returncode == 2 and 'requires --idempotency-key' in missing.stderr


def test_skill_helper_rejects_operator_routes_missing_keys_and_remote_http():
    denied = invoke('http://127.0.0.1:1', 'POST', '/approvals/00000000-0000-0000-0000-000000000001/resolve', '--data-file', '-', body={})
    assert denied.returncode == 2 and 'Unsupported agent endpoint' in denied.stderr
    contract_write = invoke('http://127.0.0.1:1', 'POST', '/tasks/00000000-0000-0000-0000-000000000001/contract', '--data-file', '-', body={})
    assert contract_write.returncode == 2 and 'Unsupported agent endpoint' in contract_write.stderr
    missing = invoke('http://127.0.0.1:1', 'POST', '/tasks', '--data-file', '-', body={})
    assert missing.returncode == 2 and 'requires --idempotency-key' in missing.stderr
    remote = invoke('http://example.com', 'GET', '/state')
    assert remote.returncode == 2 and 'require HTTPS' in remote.stderr
