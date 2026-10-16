"""Task decomposition is durable bookkeeping, with explicit independent lifecycles."""
from uuid import uuid4
import json
import pytest
from sqlalchemy import select
from typer.testing import CliRunner
from hsm.app import create_app
from hsm.cli import app as cli
from hsm.database import transaction
from hsm.models import Task
from hsm.hierarchy import task_forest


def transition(call, task, *states):
    for state in states:
        response = call('POST', f"/tasks/{task['id']}/transition", {
            'expected_version': task['version'], 'state': state}, key=str(uuid4()))
        assert response.status_code == 200, response.json
        task = response.json['data']
    return task


def test_multi_agent_tree_progress_and_independent_lifecycles(env, make_task):
    call = env[2]
    owner1 = call('POST', '/agent-sessions', {'project_id': 'alpha', 'profile': 'schema'}).json['data']
    owner2 = call('POST', '/agent-sessions', {'project_id': 'alpha', 'profile': 'worker'}).json['data']
    root = make_task(task_key='FEATURE-1', title='Larger feature')
    schema = make_task(task_key='FEATURE-1-SCHEMA', parent_id=root['id'], owner_session_id=owner1['id'])
    worker = make_task(task_key='FEATURE-1-WORKER', parent_id=root['id'], owner_session_id=owner2['id'])
    nested = make_task(task_key='FEATURE-1-TEST', parent_id=worker['id'])
    pane = call('POST', '/panes', {'project_id': 'alpha', 'external_pane_id': 'test-only',
                                  'task_id': schema['id'], 'agent_session_id': owner1['id']}).json['data']
    transition(call, schema, 'planning', 'running', 'review', 'completed')
    transition(call, worker, 'blocked')
    transition(call, nested, 'failed')
    tree = call('GET', f"/tasks/{root['id']}/tree", token='a').json['data']
    assert tree['root_ids'] == [root['id']] and tree['truncation']['nodes'] is False
    assert [(t['task_key'], t['depth']) for t in tree['nodes']] == [
        ('FEATURE-1', 0), ('FEATURE-1-SCHEMA', 1), ('FEATURE-1-WORKER', 1), ('FEATURE-1-TEST', 2)]
    parent = tree['nodes'][0]
    assert parent['child_summary']['total'] == 2
    assert parent['descendant_summary']['total'] == 3
    assert parent['descendant_summary']['completed'] == 1
    assert parent['descendant_summary']['blocked'] == 1
    assert parent['descendant_summary']['failed'] == 1
    assert parent['state'] == 'queued' and parent['version'] == 1
    assert tree['nodes'][1]['owner_profile'] == 'schema' and tree['nodes'][1]['pane_ids'] == [pane['id']]
    assert tree['nodes'][2]['owner_profile'] == 'worker'
    subtree = call('GET', f"/tasks/{worker['id']}/tree").json['data']
    assert [n['depth'] for n in subtree['nodes']] == [0, 1]
    assert call('GET', '/tasks?parent_id=' + root['id']).json['pagination']['has_more'] is False
    assert len(call('GET', '/tasks?parent_id=' + root['id']).json['data']) == 2
    assert [t['id'] for t in call('GET', '/tasks?project=alpha&parent_id=none').json['data']] == [root['id']]


def test_snapshot_recovery_retains_terminal_ancestors_and_closed_progress(env, make_task):
    call = env[2]
    parent = transition(call, make_task(task_key='OLD'), 'cancelled')
    child = make_task(task_key='FOLLOWUP', parent_id=parent['id'])
    completed = transition(call, make_task(task_key='DONE', parent_id=child['id']), 'planning', 'running', 'review', 'completed')
    snapshot = call('GET', '/state?project=alpha').json['data']
    assert [t['id'] for t in snapshot['active_tasks']] == [child['id']]
    hierarchy = snapshot['task_hierarchy']
    assert hierarchy['selection'] == 'active_with_ancestors'
    assert [t['id'] for t in hierarchy['nodes']] == [parent['id'], child['id']]
    assert hierarchy['nodes'][1]['descendant_summary']['completed'] == 1
    assert any('terminal parent' in warning for warning in snapshot['warnings'])
    restarted = create_app(env[0])
    recovered = restarted.test_client().get('/api/v1/state?project=alpha', headers={'Authorization': 'Bearer ' + 'a' * 32}).json['data']
    assert recovered['task_hierarchy'] == hierarchy
    restarted.extensions['hsm_engine'].dispose()


def test_hierarchy_scopes_validation_reparent_and_stale_events(env, make_task):
    call = env[2]
    alpha = make_task()
    beta = make_task('beta')
    child = make_task(parent_id=alpha['id'])
    assert call('GET', f"/tasks/{beta['id']}/tree", token='a').status_code == 403
    assert call('GET', '/tasks?parent_id=' + beta['id'], token='a').status_code == 403
    assert call('GET', '/tasks?parent_id=bogus').status_code == 422
    assert call('GET', '/tasks/tree?limit=2').status_code == 422
    assert call('GET', f"/tasks/{alpha['id']}/tree?project=beta").status_code == 409
    assert {n['project_id'] for n in call('GET', '/tasks/tree', token='a').json['data']['nodes']} == {'alpha'}
    assert call('PATCH', '/tasks/' + child['id'], {'expected_version': 1, 'parent_id': beta['id']}).status_code == 409
    assert call('PATCH', '/tasks/' + alpha['id'], {'expected_version': 1, 'parent_id': child['id']}).status_code == 409
    new = make_task()
    response = call('PATCH', '/tasks/' + child['id'], {'expected_version': 1, 'parent_id': new['id']})
    assert response.status_code == 200 and response.json['data']['parent_key'] == new['task_key']
    event = call('GET', f"/tasks/{child['id']}/events").json['data'][0]
    assert event['details']['parent_id'] == {'previous_id': alpha['id'], 'new_id': new['id']}
    before = call('GET', f"/tasks/{child['id']}/events").json
    assert call('PATCH', '/tasks/' + child['id'], {'expected_version': 1, 'parent_id': None}).status_code == 409
    assert call('GET', f"/tasks/{child['id']}/events").json == before
    detached = call('PATCH', '/tasks/' + child['id'], {'expected_version': 2, 'parent_id': None}).json['data']
    assert detached['depth'] == 0 and detached['parent_key'] is None


def test_cli_subtask_and_tree_with_rest_transport(env, make_task, monkeypatch):
    from hsm.client import Client
    parent = make_task(task_key='PARENT')
    def request(self, path, params=None, method='GET', data=None, idempotency_key=None):
        from urllib.parse import urlencode
        result = env[2](method, path + ('?' + urlencode({k: v for k, v in params.items() if v is not None}) if params else ''), data, key=idempotency_key)
        assert result.status_code < 400, result.json
        return result.json
    monkeypatch.setattr(Client, 'request', request)
    runner = CliRunner()
    args = ['subtask', 'PARENT', '--project', 'alpha', '--key', 'CHILD', '--title', 'Worker piece',
            '--objective', 'Part of larger feature', '--idempotency-key', 'cli-child', '--json']
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    child = json.loads(result.output)['data']
    assert child['parent_id'] == parent['id']
    assert runner.invoke(cli, args).exit_code == 0
    tree = runner.invoke(cli, ['task', 'PARENT', '--project', 'alpha', '--tree'])
    assert tree.exit_code == 0 and 'CHILD' in tree.output and '0/1 descendants completed' in tree.output
    full = runner.invoke(cli, ['tasks', '--tree', '--project', 'alpha', '--json'])
    assert [n['task_key'] for n in json.loads(full.output)['data']['nodes']] == ['PARENT', 'CHILD']
    only_children = runner.invoke(cli, ['tasks', '--project', 'alpha', '--parent', 'PARENT', '--json'])
    assert only_children.exit_code == 0, only_children.exception
    assert len(json.loads(only_children.output)['data']) == 1
    assert runner.invoke(cli, ['subtask', parent['id'], '--project', 'beta', '--key', 'WRONG', '--title', 'X', '--objective', 'X', '--idempotency-key', 'wrong']).exit_code != 0


def test_deep_hierarchy_and_cycle_rejection():
    tasks = [{'id': str(i), 'project_id': 'p', 'task_key': str(i), 'state': 'queued',
              'parent_id': str(i - 1) if i else None} for i in range(1100)]
    nodes = task_forest(tasks)
    assert nodes[-1]['depth'] == 1099
    assert nodes[0]['descendant_summary']['active'] == 1099
    tasks[0]['parent_id'] = '1099'
    with pytest.raises(ValueError):
        task_forest(tasks)
