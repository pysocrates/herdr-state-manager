"""Recovery is durable observation, never restoration of execution."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import subprocess
import sys
import pytest
from sqlalchemy import select
from hsm.database import transaction
from hsm.models import Base, Event, Task
from hsm import reporting
from test_governance import contract
from test_durable import post, artifact, validate, exception
from test_interventions import intervene


def read(call, task):
    response = call('GET', '/tasks/' + task['id'] + '/recovery', token='a')
    assert response.status_code == 200, response.json
    return response.json['data']


def transition(call, task, state):
    response = post(call, '/tasks/' + task['id'] + '/transition',
                    {'expected_version': task['version'], 'state': state})
    assert response.status_code == 200, response.json
    return response.json['data']


def checkpoint(call, task, item=None, action=None):
    response = post(call, '/tasks/' + task['id'] + '/checkpoints',
        {'expected_version': task['version'], 'revision_ids': [item['revisions'][0]['id']] if item else [],
         'next_authorized_action': action, 'blockers': ['operator review']})
    assert response.status_code == 201, response.json
    return response.json['data']


def dump(engine):
    with transaction(engine) as db:
        return {table.name: [tuple(row) for row in db.execute(select(table))]
                for table in Base.metadata.sorted_tables}


def test_restart_replacement_paused_terminal_and_pure_reads(env, make_task):
    cfg, app, call = env
    owner = call('POST', '/agent-sessions', {'project_id': 'alpha', 'profile': 'old'}).json['data']
    task = make_task(owner_session_id=owner['id'])
    revision = contract(call, task, {'required_deliverables': ['result'], 'max_children': 2, 'stop_when_satisfied': False}).json['data']['contract']
    task = call('GET', '/tasks/' + task['id']).json['data']
    item = artifact(call, task)
    assert validate(call, item).status_code == 201
    cp = checkpoint(call, task, item, 'record_evidence')
    exc = exception(call, task)
    assert call('PATCH', '/agent-sessions/' + owner['id'], {'runtime_status': 'terminated'}).status_code == 200
    new_owner = call('POST', '/agent-sessions', {'project_id': 'alpha', 'profile': 'replacement'}).json['data']
    task = call('PATCH', '/tasks/' + task['id'], {'expected_version': task['version'],
                 'owner_session_id': new_owner['id']}).json['data']
    view = read(call, task)
    assert view['ownership']['owner_session_id'] == new_owner['id']
    assert view['next_authorized_action'] is None and view['next_action_reason'] == 'task_version_changed'
    task = intervene(call, task).json['data']
    assert read(call, task)['next_action_reason'] == 'governance_paused'
    task = transition(call, task, 'failed')
    before = dump(app.extensions['hsm_engine'])
    view = read(call, task)
    assert view['task']['state'] == 'failed' and view['task']['governance']['effective_paused']
    assert view['local_contract']['id'] == revision['id']
    assert view['latest_checkpoint'] == cp
    assert view['deliverables'][0]['validated']
    assert view['unresolved_exceptions'][0]['id'] == exc['id']
    assert view['next_action_reason'] == 'terminal_task' and view['next_authorized_action'] is None
    assert view['last_recorded_authorized_action']['event_type'] == 'task.transitioned'
    # A genuinely separate interpreter/engine recovers the same persisted records.
    script = '''import json,sys
from hsm.app import create_app
from hsm.config import Config
app=create_app(Config(database_url=sys.argv[1], credentials=[{'token':'a'*32,'identity':'worker-a','role':'agent','projects':['alpha'],'actions':['read','tasks:write']}]))
r=app.test_client().get('/api/v1/tasks/'+sys.argv[2]+'/recovery',headers={'Authorization':'Bearer '+'a'*32})
assert r.status_code==200
print(json.dumps(r.json['data']))
'''
    result = subprocess.run([sys.executable, '-c', script, cfg.database_url, task['id']],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    restarted = json.loads(result.stdout)
    for key in ['deliverables', 'checkpoints', 'unresolved_exceptions', 'ownership', 'local_contract', 'event_cursor']:
        assert restarted[key] == view[key]
    snapshot = call('GET', '/state?project=alpha').json['data']
    assert not snapshot['active_tasks'] and not snapshot['governance']['recovery']
    assert snapshot['governance']['metrics']['counts']['terminal_tasks'] == 1
    assert dump(app.extensions['hsm_engine']) == before


@pytest.mark.parametrize('state', ['queued', 'planning', 'running', 'awaiting_approval', 'blocked', 'review', 'completed', 'failed', 'cancelled'])
def test_all_states_auth_no_inferred_intent(env, make_task, state):
    task = make_task()
    with transaction(env[1].extensions['hsm_engine'], True) as db:
        row = db.get(Task, task['id'])
        assert row is not None
        row.state = state
    view = read(env[2], task)
    assert view['task']['state'] == state
    assert view['next_authorized_action'] is None
    assert view['next_action_reason'] == 'no_recorded_action'
    assert view['local_contract'] is None and view['contracts'] == []
    assert view['ownership']['session'] is None
    assert view['runtime']['context_utilization']['value'] is None
    assert env[2]('GET', '/tasks/' + task['id'] + '/recovery', token='b').status_code == 403
    assert env[2]('GET', '/tasks/' + task['id'] + '/recovery', token='x').status_code == 401
    assert env[2]('GET', '/tasks/' + task['id'] + '/recovery?resume=true').status_code == 422


def test_checkpoint_revalidation_evidence_contract_time_and_grants(env, make_task, monkeypatch):
    call = env[2]
    root = make_task()
    revision = contract(call, root, {'runtime_seconds': 1000}).json['data']['contract']
    task = make_task(parent_id=root['id'], spawn_reason='bounded work')
    task = transition(call, task, 'planning')
    task = transition(call, task, 'running')
    item = artifact(call, task)
    assert validate(call, item).status_code == 201
    checkpoint(call, task, item, 'review')
    instant = datetime.fromisoformat(revision['authorization_started_at'].replace('Z', '+00:00'))
    monkeypatch.setattr(reporting, 'now', lambda: (instant + timedelta(seconds=999)).isoformat())
    view = read(call, task)
    assert view['local_contract'] is None and view['contracts'][0]['id'] == revision['id']
    assert view['next_authorized_action'] == 'review'
    assert view['remaining_budget']['remaining_runtime_seconds'] == 1
    env[0].credentials[1]['actions'].remove('tasks:write')
    assert read(call, task)['next_action_reason'] == 'forbidden'
    env[0].credentials[1]['actions'].append('tasks:write')
    monkeypatch.setattr(reporting, 'now', lambda: (instant + timedelta(seconds=1000)).isoformat())
    assert read(call, task)['next_action_reason'] == 'authorization_expired'
    monkeypatch.setattr(reporting, 'now', lambda: instant.isoformat())
    updated = call('GET', '/deliverables/' + item['id']).json['data']
    assert validate(call, updated, outcome='failed').status_code == 201
    assert read(call, task)['next_action_reason'] == 'checkpoint_evidence_changed'
    root = call('GET', '/tasks/' + root['id']).json['data']
    assert contract(call, root, {}, version=1).status_code == 201
    assert read(call, task)['next_action_reason'] == 'contract_changed'


def test_completion_action_loses_readiness_without_task_version_change(env, make_task):
    call = env[2]
    task = make_task()
    assert contract(call, task, {'required_deliverables': ['result']}).status_code == 201
    task = call('GET', '/tasks/' + task['id']).json['data']
    for state in ['planning', 'running', 'review']:
        task = transition(call, task, state)
    item = artifact(call, task)
    assert validate(call, item).status_code == 201
    checkpoint(call, task, action='complete')
    assert read(call, task)['next_authorized_action'] == 'complete'
    assert post(call, '/deliverables/' + item['id'] + '/revisions', {'expected_version': 2,
        'reference': 'new', 'artifact_identity': 'new'}).status_code == 201
    view = read(call, task)
    assert view['next_authorized_action'] is None
    assert view['next_action_reason'] == 'required_deliverables_missing'
    assert view['readiness']['missing_required_keys'] == ['result']


@pytest.mark.parametrize('snapshot', [False, True])
def test_read_transaction_consistent_across_concurrent_commit(env, make_task, monkeypatch, snapshot):
    task = make_task()
    app, call = env[1:]
    original = reporting.scope
    written = False
    def interleaved(service, ident, timestamp=None):
        nonlocal written
        # Recovery's authorized task lookup has already established the read snapshot.
        if not written:
            written = True
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(lambda: call('PATCH', '/tasks/' + task['id'],
                    {'expected_version': 1, 'title': 'concurrently committed'})).result(timeout=10)
                assert result.status_code == 200
        return original(service, ident, timestamp)
    monkeypatch.setattr(reporting, 'scope', interleaved)
    if snapshot:
        data = call('GET', '/state?project=alpha', token='a').json['data']
        view = data['governance']['recovery'][0]
        assert data['active_tasks'][0]['version'] == 1
        assert data['revision'] == data['governance']['metrics']['event_cursor'] == view['event_cursor']
    else:
        view = read(call, task)
    assert view['task']['title'] == 'Task' and view['task']['version'] == 1
    assert view['last_recorded_authorized_action']['event_type'] == 'task.created'
    assert view['event_cursor'] == view['last_recorded_authorized_action']['cursor']
    fresh = read(call, task)
    assert fresh['task']['version'] == 2 and fresh['event_cursor'] > view['event_cursor']


def test_snapshot_shared_time_cursor_stable_schema_and_no_authorization_history(env, make_task, monkeypatch):
    task = make_task()
    assert contract(env[2], task, {'runtime_seconds': 1000}).status_code == 201
    fixed = '2027-01-01T00:00:00.000000Z'
    monkeypatch.setattr('hsm.services.now', lambda: fixed)
    snapshot = env[2]('GET', '/state?project=alpha&recent_events=1').json['data']
    governance = snapshot['governance']
    assert snapshot['schema_version'] == governance['schema_version'] == 1
    assert snapshot['generated_at'] == governance['as_of'] == governance['metrics']['as_of'] == fixed
    assert governance['event_cursor'] == governance['metrics']['event_cursor'] == snapshot['revision']
    assert governance['recovery'][0]['as_of'] == governance['recovery'][0]['generated_at'] == fixed
    assert governance['recovery'][0]['remaining_budget']['remaining_runtime_seconds'] == 0
    assert snapshot['task_hierarchy']['selection'] == governance['selection'] == 'active_with_ancestors'
    assert snapshot == env[2]('GET', '/state?project=alpha&recent_events=1').json['data']
    # Imported legacy task with no history must not acquire fictional authorization.
    with transaction(env[1].extensions['hsm_engine'], True) as db:
        db.query(Event).filter(Event.task_id == task['id']).delete()
    assert read(env[2], task)['last_recorded_authorized_action'] is None
