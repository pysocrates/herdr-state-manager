"""Current reference-read authority must also hold before idempotent replay."""
import pytest
from test_durable import artifact, validate


@pytest.mark.parametrize('operation', ['evidence', 'supersede', 'checkpoint_refs', 'checkpoint_action'])
@pytest.mark.parametrize('revoked', ['read', 'tasks:write'])
def test_durable_replay_rechecks_required_grants(env, make_task, operation, revoked):
    call = env[2]
    task = make_task()
    item = artifact(call, task)
    revision = item['revisions'][-1]
    if operation == 'evidence':
        path = f"/deliverables/{item['id']}/evidence"
        data = {'expected_version': 1, 'revision_id': revision['id'],
                'artifact_identity': revision['artifact_identity'], 'outcome': 'passed', 'reference': 'opaque:proof'}
    elif operation == 'supersede':
        target = artifact(call, task, key='replacement')
        path = f"/deliverables/{item['id']}/supersede"
        data = {'expected_version': 1, 'superseded_by': target['id']}
    else:
        assert validate(call, item).status_code == 201
        path = f"/tasks/{task['id']}/checkpoints"
        data = {'expected_version': 1, 'revision_ids': [revision['id']] if operation == 'checkpoint_refs' else [],
                'next_authorized_action': 'record_evidence' if operation == 'checkpoint_action' else None}
    first = call('POST', path, data, token='a', key='logical-operation')
    assert first.status_code == 201, first.json
    events = call('GET', f"/tasks/{task['id']}/events").json
    actions = env[0].credentials[1]['actions']
    actions.remove(revoked)
    replay = call('POST', path, data, token='a', key='logical-operation')
    assert replay.status_code == 403
    assert replay.json['error']['code'] == 'forbidden'
    assert call('GET', f"/tasks/{task['id']}/events").json == events
    actions.append(revoked)
    assert call('POST', path, data, token='a', key='logical-operation').json == first.json
    assert call('GET', f"/tasks/{task['id']}/events").json == events


def test_checkpoint_without_reference_reads_preserves_write_only_compatibility(env, make_task):
    task = make_task()
    env[0].credentials[1]['actions'].remove('read')
    path = f"/tasks/{task['id']}/checkpoints"
    data = {'expected_version': 1, 'revision_ids': []}
    first = env[2]('POST', path, data, token='a', key='write-only-checkpoint')
    assert first.status_code == 201
    assert env[2]('POST', path, data, token='a', key='write-only-checkpoint').json == first.json
