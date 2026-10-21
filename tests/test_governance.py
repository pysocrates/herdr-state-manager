from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from sqlalchemy import select, func, text
from hsm.database import transaction, migrate, engine_for
from hsm.models import Task, TaskContract, Delegation, Event


def contract(call, task, spec=None, version=0, key=None, token='o'):
    return call('POST', f"/tasks/{task['id']}/contract", {
        'expected_version': task['version'], 'expected_contract_version': version,
        'reason': 'Operator authorized scope', 'contract': {'objective': task['objective'], **(spec or {})}},
        key=key or str(uuid4()), token=token)


def child(call, parent, **extra):
    return call('POST', '/tasks', {'project_id': 'alpha', 'task_key': str(uuid4()),
        'title': 'child', 'objective': 'delegated work', 'parent_id': parent['id'],
        'spawn_reason': 'decompose', **extra}, key=str(uuid4()))


def test_contract_history_authority_and_stale(env, make_task):
    call = env[2]
    task = make_task()
    assert contract(call, task, token='a').status_code == 403
    assert contract(call, task, token='b').status_code == 403
    first = contract(call, task, {'max_children': 2}, key='first')
    assert first.status_code == 201
    assert contract(call, task, {'max_children': 2}, key='first').json == first.json
    assert contract(call, task).status_code == 409
    updated = call('GET', '/tasks/' + task['id']).json['data']
    second = contract(call, updated, {'max_children': 3}, version=1)
    assert second.status_code == 201
    history = call('GET', f"/tasks/{task['id']}/contract").json['data']
    assert [r['revision'] for r in history['history']] == [1, 2]
    assert history['history'][0] == first.json['data']['contract']
    assert history['current']['actor'] == 'operator'
    assert history['current']['expected_task_version'] == 2
    assert history['current']['expected_contract_version'] == 1
    assert history['current']['authorization_started_at'] == history['history'][0]['authorization_started_at']


@pytest.mark.parametrize('spec', [
    {'max_children': -1}, {'max_children': True}, {'max_descendant_depth': -1},
    {'runtime_seconds': False}, {'retry_budget': -2}, {'unknown': 1},
    {'resource_labels': ['x'] * 101}, {'required_deliverables': ['x'] * 101}])
def test_strict_contract_values(env, make_task, spec):
    assert contract(env[2], make_task(), spec).status_code == 422


def test_lifetime_depth_scope_and_provenance(env, make_task):
    call = env[2]
    root = make_task()
    result = contract(call, root, {'max_children': 1, 'max_descendant_depth': 1, 'resource_labels': ['repo']})
    assert result.status_code == 201
    assert child(call, root, resource_labels=['other']).json['error']['code'] == 'resource_scope'
    assert child(call, root, repository_path='repo/sub').json['error']['code'] == 'resource_scope'
    assert child(call, root, spawn_reason=None).json['error']['code'] == 'spawn_reason_required'
    created = child(call, root, resource_labels=['repo'])
    assert created.status_code == 201
    task = created.json['data']
    provenance = task['delegation_history'][0]
    assert provenance['authorized_by'] == 'operator'
    assert provenance['parent_contract_id'] == result.json['data']['contract']['id']
    assert child(call, task).json['error']['code'] == 'descendant_depth'
    assert call('PATCH', '/tasks/' + task['id'], {'expected_version': 1, 'parent_id': None}).json['error']['code'] == 'governed_reparent'
    assert call('POST', f"/tasks/{task['id']}/transition", {'expected_version': 1, 'state': 'cancelled'}, key='cancel').status_code == 200
    assert child(call, root).json['error']['code'] == 'child_budget'
    assert call('GET', '/tasks/' + task['id']).json['data']['delegation_history'][0] == provenance
    tree = call('GET', f"/tasks/{task['id']}/tree").json['data']['nodes'][0]
    assert tree['depth'] == 0 and tree['delegation_depth'] == 1


def test_denial_is_durable_sanitized_and_replayed(env, make_task):
    call = env[2]
    task = make_task()
    revision = contract(call, task, {'max_children': 0})
    assert revision.status_code == 201
    payload = {'project_id': 'alpha', 'task_key': 'secret-value', 'title': 'secret-value',
               'objective': 'secret-value', 'parent_id': task['id'], 'spawn_reason': 'secret-value'}
    for _ in range(2):
        assert call('POST', '/tasks', payload, key='denied').status_code == 409
    with transaction(env[1].extensions['hsm_engine']) as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 1
        assert db.scalar(select(func.count()).select_from(Delegation)) == 0
        denials = list(db.scalars(select(Event).where(Event.event_type == 'governance.denied')))
        assert len(denials) == 1
        assert denials[0].details == {'code': 'child_budget',
            'governing_contract_ids': [revision.json['data']['contract']['id']]}
        assert 'secret-value' not in str(denials[0].details)
    assert call('POST', '/tasks', payload, token='b', key='forbidden').status_code == 403
    with transaction(env[1].extensions['hsm_engine']) as db:
        assert db.scalar(select(func.count()).select_from(Event).where(Event.event_type == 'governance.denied')) == 1


def test_scope_patch_and_subtree_moves(env, make_task):
    call = env[2]
    root = make_task()
    leaf = make_task(parent_id=root['id'])
    other = make_task()
    assert contract(call, leaf).status_code == 201
    # A contract deeper in the moved subtree cannot be stripped of ancestry.
    assert call('PATCH', '/tasks/' + root['id'], {'expected_version': 1, 'parent_id': other['id']}).json['error']['code'] == 'governed_reparent'
    assert call('PATCH', '/tasks/' + leaf['id'], {'expected_version': 2, 'objective': 'expand'}).json['error']['code'] == 'contract_amendment_required'
    assert call('PATCH', '/tasks/' + other['id'], {'expected_version': 1, 'parent_id': leaf['id']}).json['error']['code'] == 'governed_reparent'
    assert call('PATCH', '/tasks/' + root['id'], {'expected_version': 1, 'parent_id': leaf['id']}).json['error']['code'] == 'parent_cycle'
    assert call('PATCH', '/tasks/' + other['id'], {'expected_version': 1, 'parent_id': make_task(project='beta')['id']}).json['error']['code'] == 'cross_project'
    assert contract(call, root, {'max_descendant_depth': 0}).json['error']['code'] == 'descendant_depth'


def test_persisted_window_and_safe_exit(env, make_task):
    call = env[2]
    task = make_task()
    assert contract(call, task, {'runtime_seconds': 0}).status_code == 201
    assert child(call, task).json['error']['code'] == 'authorization_expired'
    assert call('POST', f"/tasks/{task['id']}/transition", {'expected_version': 2, 'state': 'planning'}, key='expired').json['error']['code'] == 'authorization_expired'
    assert call('PATCH', '/tasks/' + task['id'], {'expected_version': 2, 'title': 'changed'}).status_code == 409
    assert call('POST', f"/tasks/{task['id']}/transition", {'expected_version': 2, 'state': 'cancelled'}, key='safe').status_code == 200


def test_atomic_last_slot_and_contract_revision(env, make_task):
    cfg, app, call = env
    task = make_task()
    assert contract(call, task, {'max_children': 1}).status_code == 201
    def create(i):
        with app.test_client() as client:
            return client.post('/api/v1/tasks', json={'project_id': 'alpha', 'task_key': f'race-{i}',
                'title': 'race', 'objective': 'race', 'parent_id': task['id'], 'spawn_reason': 'race'},
                headers={'Authorization': 'Bearer ' + 'a' * 32, 'Idempotency-Key': f'race-{i}'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create, range(2))) == [201, 409]
    fresh = call('GET', '/tasks/' + task['id']).json['data']
    def amend(i):
        with app.test_client() as client:
            return client.post(f"/api/v1/tasks/{task['id']}/contract", json={
                'expected_version': fresh['version'], 'expected_contract_version': 1,
                'reason': 'race', 'contract': {'objective': 'race', 'max_children': 2}},
                headers={'Authorization': 'Bearer ' + 'o' * 32, 'Idempotency-Key': f'amend-{i}'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(amend, range(2))) == [201, 409]


def test_legacy_reparent_preserves_original(env, make_task):
    call = env[2]
    first, second = make_task(), make_task()
    task = make_task(parent_id=first['id'])
    moved = call('PATCH', '/tasks/' + task['id'], {'expected_version': 1, 'parent_id': second['id']})
    assert moved.status_code == 200
    assert [d['parent_id'] for d in moved.json['data']['delegation_history']] == [first['id'], second['id']]
    assert contract(call, first, {'max_children': 0}).json['error']['code'] == 'child_budget'


def test_migration_populated_0003(tmp_path):
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    url = f'sqlite:///{tmp_path}/upgrade.db'
    cfg = Config(str(root / 'alembic.ini'))
    cfg.set_main_option('script_location', str(root / 'migrations'))
    cfg.set_main_option('sqlalchemy.url', url)
    command.upgrade(cfg, '0003')
    engine = engine_for(url)
    parent, leaf = str(uuid4()), str(uuid4())
    with transaction(engine, True) as db:
        db.execute(text("INSERT INTO projects (id, name, configuration, enabled, created_at) VALUES ('alpha', 'Alpha', '{}', 1, '2026-01-01T00:00:00Z')"))
        for ident, ancestor in [(parent, None), (leaf, parent)]:
            db.execute(text("INSERT INTO tasks (id, project_id, task_key, title, objective, state, parent_id, created_at, updated_at, version) VALUES (:id, 'alpha', :id, 'old', 'old', 'queued', :parent, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 1)"), {'id': ident, 'parent': ancestor})
    migrate(url)
    with transaction(engine) as db:
        assert db.get(Task, leaf).resource_labels == []
        assert db.get(Task, leaf).parent_id == parent
        record = db.scalar(select(Delegation))
        assert record.parent_id == parent and record.task_id == leaf
        assert record.authorized_by == 'migration:unknown'
        assert record.parent_contract_id is None
        assert db.scalar(select(func.count()).select_from(TaskContract)) == 0
        assert db.execute(text('PRAGMA foreign_key_check')).all() == []
    engine.dispose()


def test_window_survives_restart_and_child_replay(env, make_task):
    from hsm.app import create_app
    call = env[2]
    task = make_task()
    assert contract(call, task, {'runtime_seconds': 100, 'max_children': 1}).status_code == 201
    payload = {'project_id': 'alpha', 'task_key': 'replay', 'title': 'child', 'objective': 'child',
               'parent_id': task['id'], 'spawn_reason': 'decompose'}
    first = call('POST', '/tasks', payload, key='success')
    assert first.status_code == 201
    assert call('POST', '/tasks', payload, key='success').json == first.json
    with transaction(env[1].extensions['hsm_engine'], True) as db:
        assert db.scalar(select(func.count()).select_from(Delegation)) == 1
        # Administrative test fixture simulates persisted elapsed wall time.
        db.scalar(select(TaskContract)).authorization_started_at = '2000-01-01T00:00:00Z'
    restarted = create_app(env[0])
    try:
        response = restarted.test_client().post(f"/api/v1/tasks/{task['id']}/transition",
            json={'expected_version': 2, 'state': 'planning'},
            headers={'Authorization': 'Bearer ' + 'a' * 32, 'Idempotency-Key': 'restart'})
        assert response.json['error']['code'] == 'authorization_expired'
    finally:
        restarted.extensions['hsm_engine'].dispose()


def test_ancestor_amendment_scope_and_atomic_rollback(env, make_task):
    call = env[2]
    root = make_task()
    assert contract(call, root, {'resource_labels': ['repo']}).status_code == 201
    task = child(call, root, resource_labels=['repo']).json['data']
    denied = contract(call, task, {'objective': 'secret-change', 'resource_labels': ['outside']})
    assert denied.json['error']['code'] == 'resource_scope'
    assert call('GET', '/tasks/' + task['id']).json['data']['objective'] == task['objective']
    assert call('GET', f"/tasks/{task['id']}/contract").json['data']['history'] == []
    fresh = call('GET', '/tasks/' + root['id']).json['data']
    assert contract(call, fresh, {'resource_labels': []}, version=1).status_code == 409
    assert call('GET', f"/tasks/{root['id']}/contract").json['data']['current']['revision'] == 1


def test_savepoint_denial_discards_partial_mutation(env, make_task, monkeypatch):
    from hsm import governance
    call = env[2]
    task = make_task()
    def injected_check(service, data):
        item = service.db.get(Task, task['id'])
        item.objective = 'must not persist'
        service.db.flush()
        governance.deny(item, 'resource_scope')
    monkeypatch.setattr(governance, 'check_create', injected_check)
    assert child(call, task).status_code == 409
    assert call('GET', '/tasks/' + task['id']).json['data']['objective'] == task['objective']
    with transaction(env[1].extensions['hsm_engine']) as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 1
        assert db.scalar(select(func.count()).select_from(Event).where(Event.event_type == 'governance.denied')) == 1


def test_ancestor_provenance_and_amendment_does_not_refill(env, make_task):
    call = env[2]
    root = make_task()
    revision = contract(call, root, {'max_children': 1, 'max_descendant_depth': 2}).json['data']['contract']
    middle = child(call, root).json['data']
    leaf = child(call, middle).json['data']
    lineage = leaf['delegation_history'][0]
    assert lineage['parent_contract_id'] is None
    assert lineage['governing_contract_ids'] == [revision['id']]
    assert child(call, leaf).json['error']['code'] == 'descendant_depth'
    fresh = call('GET', '/tasks/' + root['id']).json['data']
    assert contract(call, fresh, {'max_children': 1, 'max_descendant_depth': 2}, version=1).status_code == 201
    assert child(call, root).json['error']['code'] == 'child_budget'


def test_amendment_races_delegation(env, make_task):
    app, call = env[1], env[2]
    task = make_task()
    assert contract(call, task, {'max_children': 1}).status_code == 201
    def operation(kind):
        headers = {'Authorization': 'Bearer ' + 'o' * 32, 'Idempotency-Key': kind}
        with app.test_client() as client:
            if kind == 'amend':
                return client.post(f"/api/v1/tasks/{task['id']}/contract", json={
                    'expected_version': 2, 'expected_contract_version': 1,
                    'reason': 'close delegation', 'contract': {'objective': task['objective'], 'max_children': 0}},
                    headers=headers).status_code
            return client.post('/api/v1/tasks', json={'project_id': 'alpha', 'task_key': 'race-child',
                'title': 'child', 'objective': 'child', 'parent_id': task['id'], 'spawn_reason': 'decompose'},
                headers=headers).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(operation, ['amend', 'delegate']))
    assert sorted(results) == [201, 409]
    with transaction(app.extensions['hsm_engine']) as db:
        count = db.scalar(select(func.count()).select_from(Delegation))
        latest = db.scalar(select(TaskContract).order_by(TaskContract.revision.desc()))
        assert (count, latest.specification['max_children']) == ((0, 0) if results[0] == 201 else (1, 1))


def test_readiness(env):
    assert env[2]('GET', '/health/ready').json['data']['schema_revision'] == '0004'


@pytest.mark.parametrize('spec', [
    {'prohibited_operations': ['x'] * 101}, {'prohibited_operations': ['']},
    {'prohibited_operations': ['x' * 201]}, {'stop_when_satisfied': 1},
    {'expansion_requires_authorization': 'false'}])
def test_new_contract_fields_strict(env, make_task, spec):
    assert contract(env[2], make_task(), spec).status_code == 422


def test_new_fields_roundtrip_defaults_and_legacy_json(env, make_task):
    call = env[2]
    task = make_task()
    first = contract(call, task).json['data']['contract']
    assert first['specification']['prohibited_operations'] == []
    assert first['specification']['stop_when_satisfied'] is True
    assert first['specification']['expansion_requires_authorization'] is True
    with transaction(env[1].extensions['hsm_engine'], True) as db:
        record = db.get(TaskContract, first['id'])
        record.specification = {k: v for k, v in record.specification.items()
            if k not in {'prohibited_operations', 'stop_when_satisfied', 'expansion_requires_authorization'}}
    assert child(call, task).status_code == 201
    updated = call('PATCH', '/tasks/' + task['id'], {'expected_version': 2, 'title': 'legacy'}).json['data']
    assert call('POST', f"/tasks/{task['id']}/transition",
        {'expected_version': 3, 'state': 'planning'}, key='legacy-transition').status_code == 200
    updated = call('GET', '/tasks/' + task['id']).json['data']
    spec = {'prohibited_operations': ['external.tool'], 'stop_when_satisfied': False,
            'expansion_requires_authorization': False}
    second = contract(call, updated, spec, version=1).json['data']['contract']
    for key, value in spec.items():
        assert second['specification'][key] == value
    assert child(call, task).status_code == 201  # unknown labels are advisory
    assert call('PATCH', '/tasks/' + task['id'], {'expected_version': 5,
        'objective': 'implicit expansion'}).json['error']['code'] == 'contract_amendment_required'
    history = call('GET', f"/tasks/{task['id']}/contract").json['data']
    assert history['current'] == second


@pytest.mark.parametrize('operation', ['task.create', 'task.patch', 'task.transition',
                                      'task.transition.planning'])
def test_prohibited_operations_are_atomic_and_inherited(env, make_task, operation):
    call = env[2]
    root = make_task()
    leaf = child(call, root).json['data']
    revision = contract(call, root, {'prohibited_operations': [operation]}).json['data']['contract']
    before = call('GET', '/tasks/' + leaf['id']).json['data']
    if operation == 'task.create':
        response = child(call, leaf)
    elif operation == 'task.patch':
        response = call('PATCH', '/tasks/' + leaf['id'], {'expected_version': 1, 'title': 'forbidden'})
    else:
        response = call('POST', f"/tasks/{leaf['id']}/transition",
            {'expected_version': 1, 'state': 'planning'}, key='forbidden-transition')
    assert response.status_code == 409
    assert response.json['error']['code'] == 'prohibited_operation'
    assert call('GET', '/tasks/' + leaf['id']).json['data'] == before
    with transaction(env[1].extensions['hsm_engine']) as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 2
        assert db.scalar(select(func.count()).select_from(Delegation)) == 1
        denial = db.scalar(select(Event).where(Event.event_type == 'governance.denied'))
        assert denial.details == {'code': 'prohibited_operation', 'governing_contract_ids': [revision['id']]}


@pytest.mark.parametrize('state', ['failed', 'cancelled'])
def test_prohibitions_preserve_safe_exits(env, make_task, state):
    call = env[2]
    task = make_task()
    contract(call, task, {'prohibited_operations': ['task.transition', f'task.transition.{state}']})
    assert call('POST', f"/tasks/{task['id']}/transition",
        {'expected_version': 2, 'state': state}, key='safe-exit').status_code == 200


def test_event_revision_snapshots_survive_ancestor_and_local_amendments(env, make_task):
    call = env[2]
    root = make_task()
    ancestor = contract(call, root).json['data']['contract']
    leaf = child(call, root).json['data']
    local = contract(call, leaf, {'prohibited_operations': ['task.create']}).json['data']['contract']
    assert call('PATCH', '/tasks/' + leaf['id'], {'expected_version': 2, 'title': 'updated'}).status_code == 200
    assert call('POST', f"/tasks/{leaf['id']}/transition",
        {'expected_version': 3, 'state': 'planning'}, key='event-transition').status_code == 200
    assert child(call, leaf).status_code == 409
    with transaction(env[1].extensions['hsm_engine']) as db:
        events = list(db.scalars(select(Event).where(Event.task_id == leaf['id'])))
        snapshots = {e.cursor: dict(e.details) for e in events}
        assert {e.event_type for e in events} == {
            'task.created', 'contract.revised', 'task.updated', 'task.transitioned', 'governance.denied'}
        for event in events:
            assert event.details['governing_contract_ids'] == (
                [ancestor['id']] if event.event_type == 'task.created' else [local['id'], ancestor['id']])
    root = call('GET', '/tasks/' + root['id']).json['data']
    new_ancestor = contract(call, root, version=1).json['data']['contract']
    leaf = call('GET', '/tasks/' + leaf['id']).json['data']
    new_local = contract(call, leaf, version=1).json['data']['contract']
    with transaction(env[1].extensions['hsm_engine']) as db:
        for ident, details in snapshots.items():
            assert db.get(Event, ident).details == details
        amended = [e for e in db.scalars(select(Event).where(Event.task_id == leaf['id']))
                   if e.details.get('contract_id') == new_local['id']]
        assert len(amended) == 1
        assert amended[0].details['governing_contract_ids'] == [new_local['id'], new_ancestor['id']]
