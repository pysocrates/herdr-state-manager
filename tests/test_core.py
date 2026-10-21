import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from sqlalchemy import select, text
from typer.testing import CliRunner
from hsm.app import create_app
from hsm.cli import app as cli
from hsm.database import transaction, migrate
from hsm.models import Task, Event
from hsm.services import Service
from hsm.state_machine import TRANSITIONS


def test_auth_and_project_isolation(env, make_task):
    cfg, app, call = env
    alpha, beta = make_task(), make_task('beta')
    assert app.test_client().get('/api/v1/tasks').status_code == 401
    assert app.test_client().get('/api/v1/health/live').status_code == 200
    assert app.test_client().get('/api/v1/health/ready').status_code == 401
    assert call('GET', '/health').json['data']['status'] == 'ready'
    assert call('GET', f"/tasks/{beta['id']}", token='a').status_code == 403
    assert len(call('GET', '/tasks', token='a').json['data']) == 1
    assert call('GET', '/state?project=beta', token='a').status_code == 403
    assert [p['id'] for p in call('GET', '/state', token='a').json['data']['projects']] == ['alpha']
    assert call('POST', '/projects', {'id': 'gamma', 'name': 'Gamma'}, token='a').status_code == 403
    assert call('PATCH', f"/tasks/{alpha['id']}", {'expected_version': 1, 'state': 'running'}).status_code == 422


@pytest.mark.parametrize('source', list(TRANSITIONS))
@pytest.mark.parametrize('destination', list(TRANSITIONS))
def test_complete_transition_matrix(env, make_task, source, destination):
    task = make_task()
    with transaction(env[1].extensions['hsm_engine'], True) as db:
        db.get(Task, task['id']).state = source
    response = env[2]('POST', f"/tasks/{task['id']}/transition",
                      {'expected_version': 1, 'state': destination}, key=str(uuid4()))
    assert response.status_code == (200 if destination in TRANSITIONS[source] else 409)
    events = env[2]('GET', f"/tasks/{task['id']}/events").json['data']
    assert len(events) == (2 if response.status_code == 200 else 1)
    if response.status_code == 200:
        assert events[0]['previous_state'] == source
        assert events[0]['new_state'] == destination


def test_versions_and_idempotency(env):
    call = env[2]
    data = {'project_id': 'alpha', 'task_key': 'A-1', 'title': 'Task', 'objective': 'Goal'}
    first = call('POST', '/tasks', data, token='a', key='create')
    assert first.status_code == 201
    assert call('POST', '/tasks', data, token='a', key='create').json == first.json
    assert call('POST', '/tasks', {**data, 'title': 'Different'}, token='a', key='create').status_code == 409
    ident = first.json['data']['id']
    change = {'expected_version': 1, 'state': 'planning'}
    result = call('POST', f'/tasks/{ident}/transition', change, token='a', key='transition')
    assert result.status_code == 200
    assert call('POST', f'/tasks/{ident}/transition', change, token='a', key='transition').json == result.json
    assert call('POST', f'/tasks/{ident}/transition', change, token='a', key='stale').status_code == 409
    assert call('PATCH', f'/tasks/{ident}', {'expected_version': 1, 'title': 'Stale'}).status_code == 409
    assert len(call('GET', f'/tasks/{ident}/events').json['data']) == 2
    assert call('POST', '/tasks', {**data, 'task_key': 'A-2'}).status_code == 422


def test_real_competing_updates(env, make_task):
    task = make_task()
    def update(title):
        with env[1].test_client() as client:
            return client.patch('/api/v1/tasks/' + task['id'], json={'expected_version': 1, 'title': title},
                                headers={'Authorization': 'Bearer ' + 'a' * 32}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, ['First', 'Second'])) == [200, 409]
    assert len(env[2]('GET', f"/tasks/{task['id']}/events").json['data']) == 2


def test_relationships_and_cycles(env, make_task):
    call = env[2]
    parent = make_task()
    child = make_task(parent_id=parent['id'])
    beta = make_task('beta')
    for parent_id in (child['id'], parent['id'], beta['id']):
        assert call('PATCH', f"/tasks/{parent['id']}", {'expected_version': 1, 'parent_id': parent_id}).status_code == 409
    assert call('PATCH', f"/tasks/{parent['id']}", {'expected_version': 1, 'title': None}).status_code == 422
    assert call('GET', '/tasks?limit=0').status_code == 422
    assert call('GET', '/tasks?created_after=invalid').status_code == 422
    assert call('POST', '/tasks', {'project_id': 'alpha', 'task_key': parent['task_key'], 'title': 'Duplicate', 'objective': 'X'}, key='duplicate').status_code == 409


def test_context_recovery_restart_and_runtime(env, make_task, monkeypatch):
    cfg, app, call = env
    agent1 = call('POST', '/agent-sessions', {'project_id': 'alpha', 'profile': 'schema'}, token='a').json['data']
    agent2 = call('POST', '/agent-sessions', {'project_id': 'alpha', 'profile': 'worker'}, token='a').json['data']
    parent = make_task(owner_session_id=agent1['id'], worktree_path='/opaque/no/access')
    child = make_task(parent_id=parent['id'], owner_session_id=agent2['id'])
    pane = call('POST', '/panes', {'project_id': 'alpha', 'external_pane_id': 'verified-id',
                                  'task_id': child['id'], 'agent_session_id': agent2['id']}, token='a').json['data']
    assert call('POST', f"/panes/{pane['id']}/heartbeat", {}, token='a').json['data']['heartbeat_at']
    call('POST', f"/tasks/{parent['id']}/transition", {'expected_version': 1, 'state': 'blocked'}, key='block')
    snapshot = call('GET', '/state/projects/alpha').json['data']
    # Compare the entire additive projection at the same captured read time.
    # Preserve every original row/field assertion, including nested governance.
    monkeypatch.setattr('hsm.services.now', lambda timestamp=snapshot['generated_at']: timestamp)
    restarted = create_app(cfg)
    fresh = restarted.test_client().get('/api/v1/state/projects/alpha', headers={'Authorization': 'Bearer ' + 'a' * 32}).json['data']
    snapshot.pop('generated_at'); fresh.pop('generated_at')
    assert snapshot == fresh
    assert len(fresh['active_tasks']) == 2
    assert len(fresh['agent_sessions']) == 2
    assert fresh['blocked_tasks'][0]['id'] == parent['id']
    assert fresh['active_panes'][0]['agent_session_id'] == agent2['id']
    assert call('GET', '/panes/' + pane['id'], token='b').status_code == 403
    assert call('GET', '/agent-sessions/' + agent1['id'], token='b').status_code == 403
    restarted.extensions['hsm_engine'].dispose()


def test_snapshot_no_active_cap_and_event_truncation(env, make_task):
    for i in range(55):
        make_task()
    snapshot = env[2]('GET', '/state?recent_events=2').json['data']
    assert len(snapshot['active_tasks']) == 55
    assert len(snapshot['recent_events']) == 2
    assert snapshot['truncation']['recent_events'] is True
    assert snapshot['revision'] == snapshot['recent_events'][0]['cursor']
    assert [t['id'] for t in snapshot['active_tasks']] == sorted(t['id'] for t in snapshot['active_tasks'])


def test_consistent_read_during_write(env, make_task):
    task = make_task()
    engine = env[1].extensions['hsm_engine']
    with transaction(engine) as reader:
        service = Service(reader, env[0].credentials[0])
        before = service.snapshot()
        with transaction(engine, True) as writer:
            Service(writer, env[0].credentials[0]).patch_task(task['id'], {'expected_version': 1, 'state': 'planning'}, True)
        after = service.snapshot()
        assert before['revision'] == after['revision']
        assert after['active_tasks'][0]['state'] == 'queued'
    assert env[2]('GET', '/state').json['data']['active_tasks'][0]['state'] == 'planning'


def test_events_immutable_and_actor(env, make_task):
    task = make_task()
    call = env[2]
    event = call('GET', f"/tasks/{task['id']}/events").json['data'][0]
    assert event['actor'] == 'operator'
    assert call('PATCH', '/events/' + event['id'], {'actor': 'fake'}).status_code in (404, 405)
    assert call('POST', '/tasks', {'project_id': 'alpha', 'task_key': 'fake', 'title': 'X', 'objective': 'X', 'actor': 'operator'}, key='fake').status_code == 422


def test_validation_and_secret_safe_errors(env):
    call = env[2]
    assert call('POST', '/panes', {'project_id': 'alpha', 'external_pane_id': ''}).status_code == 422
    result = call('POST', '/projects', {'id': '../escape', 'name': 'secret-value'})
    assert result.status_code == 422 and 'secret-value' not in result.text
    assert call('POST', '/projects', {'id': 'big', 'name': 'Big', 'configuration': {'a': 'x' * 9000}}).status_code == 422
    assert call('POST', '/projects', {'id': 'huge', 'name': 'Huge', 'configuration': {'a': 'x' * 70000}}).status_code == 413


def test_cli_json_and_default_summary(monkeypatch):
    from hsm.client import Client
    fixture = {'data': {'project': None, 'revision': 4, 'active_tasks': [], 'agent_sessions': [],
                         'active_panes': [], 'pending_approvals': [], 'warnings': []}}
    monkeypatch.setattr(Client, 'request', lambda *a, **k: fixture)
    runner = CliRunner()
    result = runner.invoke(cli, ['status', '--json'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == fixture
    assert runner.invoke(cli, []).exit_code == 0
    assert 'HSM' in runner.invoke(cli, []).output


def test_migration_repeat_and_sqlite_settings(env):
    migrate(env[0].database_url)
    with transaction(env[1].extensions['hsm_engine']) as db:
        assert db.execute(text('PRAGMA journal_mode')).scalar() == 'wal'
        assert db.execute(text('PRAGMA foreign_keys')).scalar() == 1
        assert db.execute(text('PRAGMA busy_timeout')).scalar() == 5000


def test_ended_ownership_remains_recoverable(env, make_task):
    call = env[2]
    session = call('POST', '/agent-sessions', {'project_id': 'alpha', 'profile': 'worker'}).json['data']
    task = make_task(owner_session_id=session['id'])
    pane = call('POST', '/panes', {'project_id': 'alpha', 'external_pane_id': 'ending', 'task_id': task['id']}).json['data']
    call('PATCH', '/agent-sessions/' + session['id'], {'runtime_status': 'terminated'})
    call('PATCH', '/panes/' + pane['id'], {'runtime_status': 'terminated'})
    snapshot = call('GET', '/state/projects/alpha').json['data']
    assert snapshot['related_ended_sessions'][0]['profile'] == 'worker'
    assert snapshot['related_terminated_panes'][0]['task_id'] == task['id']
    assert snapshot['active_tasks'][0]['owner_session_id'] == session['id']


def test_replay_checks_current_grants(env, make_task):
    cfg, app, call = env
    payload = {'project_id': 'alpha', 'task_key': 'REPLAY', 'title': 'Replay', 'objective': 'Scope'}
    assert call('POST', '/tasks', payload, token='a', key='scope-key').status_code == 201
    cfg.credentials[1]['projects'] = []
    assert call('POST', '/tasks', payload, token='a', key='scope-key').status_code == 403


def test_backup_online_and_refuses_overwrite(env, monkeypatch, tmp_path):
    import sqlite3
    monkeypatch.setenv('HSM_DATABASE_URL', env[0].database_url)
    destination = tmp_path / 'backup.db'
    runner = CliRunner()
    assert runner.invoke(cli, ['backup', str(destination)]).exit_code == 0
    assert runner.invoke(cli, ['backup', str(destination)]).exit_code != 0
    with sqlite3.connect(destination) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('SELECT count(*) FROM projects').fetchone()[0] == 2


def test_invalid_credential_grants():
    from hsm.config import Config
    with pytest.raises(ValueError):
        Config(database_url='sqlite:////tmp/config-test-only.db', credentials=[{
            'identity': 'bad', 'token': 'x' * 32, 'role': 'agent', 'projects': ['*'], 'actions': ['read']}]).validate()
    with pytest.raises(ValueError):
        Config(database_url='sqlite:///relative.db', credentials=[]).validate()
