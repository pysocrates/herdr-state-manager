from uuid import uuid4
from datetime import datetime, timedelta, timezone
import sqlite3
import pytest
from alembic import command
from alembic.config import Config
from hsm.database import migrate
from test_governance import contract, child
from test_durable import post, artifact, validate, exception, grant, associate, authorize


def intervene(call, task, action='pause', **kw):
    return post(call, f"/tasks/{task['id']}/{action}",
                {'expected_version': task['version'], 'reason': 'Operator investigation'}, **kw)


def test_permissions_replay_versions_audit(env, make_task):
    call = env[2]
    task = make_task()
    for token in ['a', 'b']:
        assert intervene(call, task, token=token).status_code == 403
    payload = {'expected_version': 1, 'reason': 'hold'}
    path = f"/tasks/{task['id']}/pause"
    for extra in [{'reason': '  '}, {'reason': 'x'*4097}, {'expected_version': True}, {'expected_version': '1'}, {'state': 'running'}]:
        assert post(call, path, {**payload, **extra}).status_code == 422
    assert call('POST', path, payload).status_code == 422
    result = intervene(call, task, key='hold')
    assert result.status_code == 200
    paused = result.json['data']
    assert paused['version'] == 2 and paused['state'] == task['state']
    assert paused['governance']['paused'] and paused['governance_changed_by'] == 'operator'
    assert paused['governance_changed_at'] and paused['governance_reason'] == 'Operator investigation'
    assert intervene(call, task, key='hold').json == result.json
    assert intervene(call, task, 'resume').json['error']['code'] == 'stale_version'
    assert intervene(call, paused).json['error']['code'] == 'governance_conflict'
    assert post(call, path, payload, key='hold').json['error']['code'] == 'idempotency_mismatch'
    resumed = intervene(call, paused, 'resume').json['data']
    assert resumed['version'] == 3 and not resumed['governance']['paused']
    assert resumed['state'] == task['state']
    events = call('GET', f"/tasks/{task['id']}/events").json['data']
    events = [e for e in events if e['event_type'] in {'task.paused', 'task.resumed'}]
    assert len(events) == 2 and all(e['actor'] == 'operator' and e['timestamp'] and e['details']['reason'] for e in events)


def test_uncontracted_ancestry_reparent_and_contract_bypass(env, make_task):
    call = env[2]
    root = make_task()
    branch = make_task(parent_id=root['id'])
    leaf = make_task(parent_id=branch['id'])
    outside = make_task()
    paused = intervene(call, root).json['data']
    assert child(call, leaf).json['error']['code'] == 'governance_paused'
    for task, changes in [(leaf, {'parent_id': None}), (branch, {'parent_id': outside['id']}),
                          (outside, {'parent_id': leaf['id']}), (leaf, {'objective': 'escape'}),
                          (leaf, {'owner_session_id': None})]:
        r = call('PATCH', '/tasks/' + task['id'], {'expected_version': 1, **changes})
        assert r.json['error']['code'] == 'governance_paused'
    assert contract(call, leaf).json['error']['code'] == 'governance_paused'
    assert post(call, f"/tasks/{leaf['id']}/transition", {'expected_version': 1, 'state': 'planning'}).status_code == 409
    assert call('GET', '/tasks/' + leaf['id']).json['data']['governance']['effective_paused']
    local = intervene(call, leaf).json['data']
    local = intervene(call, local, 'resume').json['data']
    assert not local['governance']['paused'] and local['governance']['effective_paused']
    assert intervene(call, paused, 'resume').status_code == 200
    assert child(call, leaf).status_code == 201
    # Moving an unpaused ancestor containing a locally paused descendant is denied.
    assert intervene(call, branch).status_code == 200
    assert call('PATCH', '/tasks/' + root['id'], {'expected_version': 3, 'parent_id': outside['id']}).status_code == 409


def test_scope_artifacts_owner_and_safe_closure(env, make_task):
    call = env[2]
    owner = call('POST', '/agent-sessions', {'project_id': 'alpha', 'profile': 'worker'}).json['data']
    task = make_task(owner_session_id=owner['id'])
    assert contract(call, task, {'required_deliverables': ['result'], 'max_children': 3,
        'runtime_seconds': 3600, 'retry_budget': 2, 'max_descendant_depth': 2}).status_code == 201
    task = call('GET', '/tasks/' + task['id']).json['data']
    desc = child(call, task).json['data']
    for state in ['planning', 'running']:
        task = post(call, f"/tasks/{task['id']}/transition", {'expected_version': task['version'], 'state': state}).json['data']
    item = artifact(call, task)
    task = intervene(call, task).json['data']
    assert validate(call, item).status_code == 201
    exc = exception(call, task)
    scope = call('GET', f"/tasks/{task['id']}/scope").json['data']
    assert scope['task']['owner_session_id'] == owner['id']
    assert scope['deliverables'][0]['revisions'] == item['revisions']
    assert scope['unresolved_exceptions'][0]['id'] == exc['id']
    assert scope['runtime_control'] == {'supported': False, 'acknowledged': False}
    assert scope['task']['governance'] == {'paused': True, 'effective_paused': True,
        'remaining_children': 2, 'ready_deliverables': 1, 'required_deliverables': 1, 'unresolved_exceptions': 1}
    budget = scope['remaining_budget']
    assert 0 < budget['remaining_runtime_seconds'] <= 3600 and budget['remaining_retries'] == 2
    descendant = call('GET', f"/tasks/{desc['id']}/scope").json['data']
    assert descendant['remaining_budget']['remaining_children'] is None
    assert descendant['remaining_budget']['remaining_descendant_depth'] == 1
    assert descendant['contracts'] == scope['contracts']
    assert call('GET', f"/tasks/{task['id']}/scope", token='b').status_code == 403
    cp = post(call, f"/tasks/{task['id']}/checkpoints", {'expected_version': task['version'],
        'revision_ids': [item['revisions'][0]['id']], 'next_authorized_action': 'review'})
    assert cp.status_code == 201
    for state in ['review', 'completed']:
        task = post(call, f"/tasks/{task['id']}/transition", {'expected_version': task['version'], 'state': state}).json['data']
    assert intervene(call, task, 'resume').json['error']['code'] == 'terminal_task'
    assert intervene(call, task).json['error']['code'] == 'terminal_task'
    assert call('GET', f"/tasks/{task['id']}/scope").json['data']['deliverables'][0]['validated']
    for path in ['/tasks', '/tasks/tree', '/state']:
        data = call('GET', path).json['data']
        nodes = data if isinstance(data, list) else data.get('nodes', data.get('task_hierarchy', {}).get('nodes', []))
        assert nodes and all('governance' in node for node in nodes)


@pytest.mark.parametrize('state', ['failed', 'cancelled'])
def test_failure_cancel_and_uncontracted_scope(env, make_task, state):
    call = env[2]
    task = intervene(call, make_task()).json['data']
    scope = call('GET', f"/tasks/{task['id']}/scope").json['data']
    assert scope['contracts'] == [] and scope['task']['governance']['required_deliverables'] == 0
    assert all(v is None for k, v in scope['remaining_budget'].items() if k != 'constraints')
    assert post(call, f"/tasks/{task['id']}/transition", {'expected_version': task['version'], 'state': state}).status_code == 200


def test_grant_and_remediation_authorization_denied_without_consumption(env, make_task):
    call = env[2]
    root = make_task()
    task = make_task(parent_id=root['id'])
    exc = exception(call, task)
    approval = grant(call, exc)
    record = associate(call, exc, approval).json['data']
    generic = grant(call, exc, operation='publish', action='publish', artifact='digest')
    assert intervene(call, root).status_code == 200
    assert authorize(call, record, approval).json['error']['code'] == 'governance_paused'
    payload = {k: generic[k] for k in ['task_id', 'operation', 'target', 'action', 'artifact']}
    assert call('POST', f"/approvals/{generic['id']}/consume", {'expected_version': 2, **payload}, token='a').json['error']['code'] == 'governance_paused'
    for a in [approval, generic]:
        assert call('GET', '/approvals/' + a['id']).json['data']['consumed_at'] is None


def test_forward_migration_preserves_populated_0005(tmp_path):
    path = tmp_path / 'legacy.db'
    url = 'sqlite:///' + str(path)
    cfg = Config('alembic.ini')
    cfg.set_main_option('sqlalchemy.url', url)
    command.upgrade(cfg, '0005')
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO projects (id,name,configuration,enabled,created_at) VALUES ('p','legacy','{}',1,'then')")
        db.execute("INSERT INTO tasks (id,project_id,task_key,title,objective,state,created_at,updated_at,version,resource_labels) VALUES ('t','p','legacy','title','goal','running','then','then',7,'[]')")
        columns = [r[1] for r in db.execute('PRAGMA table_info(tasks)')]
        before = db.execute('SELECT * FROM tasks').fetchall()
    migrate(url)
    migrate(url)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT ' + ','.join(columns) + ' FROM tasks').fetchall() == before
        assert db.execute('SELECT governance_paused,governance_changed_at,governance_changed_by,governance_reason FROM tasks').fetchall() == [(0,None,None,None)]
        assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.execute('SELECT version_num FROM alembic_version').fetchone() == ('0006',)
