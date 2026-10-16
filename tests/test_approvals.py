from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from hsm.database import transaction
from hsm.models import Approval


def requested(env, make_task):
    task = make_task()
    data = {'project_id': 'alpha', 'task_id': task['id'], 'operation': 'deploy', 'target': 'staging',
            'action': 'deploy artifact abc', 'artifact': 'sha256:abc',
            'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    response = env[2]('POST', '/approvals', data, token='a', key='approval')
    assert response.status_code == 201, response.json
    return response.json['data'], data


def test_resolution_and_exact_single_use(env, make_task):
    approval, data = requested(env, make_task)
    call = env[2]
    ident = approval['id']
    assert call('POST', '/approvals', data, token='a', key='approval').json['data'] == approval
    assert call('GET', '/approvals/' + ident, token='b').status_code == 403
    assert call('POST', '/approvals/' + ident + '/resolve', {'expected_version': 1, 'decision': 'approved'}, token='a').status_code == 403
    resolution = call('POST', '/approvals/' + ident + '/resolve', {'expected_version': 1, 'decision': 'approved'})
    assert resolution.status_code == 200
    assert call('GET', '/tasks/' + data['task_id']).json['data']['state'] == 'queued'
    consume = {k: data[k] for k in ('task_id', 'operation', 'target', 'action', 'artifact')}
    consume['expected_version'] = 2
    assert call('POST', '/approvals/' + ident + '/consume', consume).status_code == 403
    for key in ('artifact', 'operation', 'target', 'action'):
        assert call('POST', '/approvals/' + ident + '/consume', {**consume, key: 'wrong'}, token='a').status_code == 409
    assert call('POST', '/approvals/' + ident + '/consume', consume, token='a').status_code == 200
    assert call('POST', '/approvals/' + ident + '/consume', consume, token='a').status_code == 409
    assert call('GET', '/approvals/' + ident, token='a').json['data']['consumed_by'] == 'worker-a'


def test_resolution_race(env, make_task):
    approval, _ = requested(env, make_task)
    def decision(value):
        with env[1].test_client() as client:
            return client.post('/api/v1/approvals/' + approval['id'] + '/resolve', json={
                'expected_version': 1, 'decision': value}, headers={'Authorization': 'Bearer ' + 'o' * 32}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(decision, ['approved', 'rejected'])) == [200, 409]


def test_consume_race(env, make_task):
    approval, data = requested(env, make_task)
    env[2]('POST', '/approvals/' + approval['id'] + '/resolve', {'expected_version': 1, 'decision': 'approved'})
    consume = {k: data[k] for k in ('task_id', 'operation', 'target', 'action', 'artifact')}
    def grant(_):
        with env[1].test_client() as client:
            return client.post('/api/v1/approvals/' + approval['id'] + '/consume',
                               json={**consume, 'expected_version': 2}, headers={'Authorization': 'Bearer ' + 'a' * 32}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(grant, range(2))) == [200, 409]


def test_expiration_and_rejection(env, make_task):
    approval, data = requested(env, make_task)
    with transaction(env[1].extensions['hsm_engine'], True) as db:
        db.get(Approval, approval['id']).expires_at = '2000-01-01T00:00:00.000000Z'
    assert env[2]('GET', '/approvals/' + approval['id']).json['data']['effective_status'] == 'expired'
    assert env[2]('POST', '/approvals/' + approval['id'] + '/resolve', {'expected_version': 1, 'decision': 'approved'}).status_code == 409
    assert env[2]('GET', '/state').json['data']['pending_approvals'] == []
    assert env[2]('POST', '/approvals', {**data, 'expires_at': '2000-01-01T00:00:00Z'}, token='a', key='expired').status_code == 422


def test_rejection_and_terminal_task_consumption(env, make_task):
    approval, data = requested(env, make_task)
    call = env[2]
    assert call('POST', '/approvals/' + approval['id'] + '/resolve', {'expected_version': 1, 'decision': 'rejected'}).status_code == 200
    consume = {k: data[k] for k in ('task_id', 'operation', 'target', 'action', 'artifact')}
    assert call('POST', '/approvals/' + approval['id'] + '/consume', {**consume, 'expected_version': 2}, token='a').status_code == 409
    assert call('POST', '/approvals', {**data, 'action': 'new action'}, token='a', key='approval').status_code == 409
    assert call('POST', '/tasks/' + data['task_id'] + '/transition', {'expected_version': 1, 'state': 'cancelled'}, key='cancel').status_code == 200
    assert call('POST', '/approvals', data, token='a', key='new-request').status_code == 409
