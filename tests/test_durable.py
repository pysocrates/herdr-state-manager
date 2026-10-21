from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from sqlalchemy import select, func, text
from hsm.models import Approval, Task, Event, Remediation, Incident
from hsm.database import transaction, migrate, engine_for
from hsm.app import create_app
from test_governance import contract, child


def post(call, path, data, **kw):
    return call('POST', path, data, key=kw.pop('key', str(uuid4())), **kw)


def artifact(call, task, key='result'):
    r = post(call, f"/tasks/{task['id']}/deliverables", {'expected_version': task['version'],
        'key': key, 'title': 'Result', 'reference': 'opaque://output', 'artifact_identity': 'sha256:one'})
    assert r.status_code == 201, r.json
    return r.json['data']


def validate(call, item, **extra):
    return post(call, f"/deliverables/{item['id']}/evidence", {'expected_version': item['version'],
        'revision_id': item['revisions'][-1]['id'], 'artifact_identity': item['revisions'][-1]['artifact_identity'],
        'outcome': 'passed', 'reference': 'test-log:one', **extra})


def exception(call, task, kind='execution'):
    r = post(call, f"/tasks/{task['id']}/exceptions", {'expected_version': task['version'],
        'failure_kind': kind, 'description': 'recorded failure'})
    assert r.status_code == 201, r.json
    return r.json['data']


def grant(call, exc, decision='approved', **extra):
    data = {'project_id': exc['project_id'], **exc['remediation_grant'],
        'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), **extra}
    r = post(call, '/approvals', data, token='a')
    assert r.status_code == 201, r.json
    item = r.json['data']
    if decision:
        r = call('POST', f"/approvals/{item['id']}/resolve", {'expected_version': 1, 'decision': decision})
        assert r.status_code == 200, r.json
        item = r.json['data']
    return item


def associate(call, exc, approval):
    return post(call, f"/exceptions/{exc['id']}/remediations", {'expected_version': exc['version'],
        'approval_id': approval['id']}, token='a')


def authorize(call, record, approval, **kw):
    return post(call, f"/remediations/{record['id']}/authorize", {'expected_version': record['version'],
        'expected_approval_version': approval['version']}, token='a', **kw)


def test_exact_revision_evidence_invalidates_and_survives_failure(env, make_task):
    call = env[2]
    task = make_task()
    item = artifact(call, task)
    assert validate(call, item, artifact_identity='sha256:other').status_code == 409
    assert validate(call, item, revision_id=str(uuid4())).status_code == 404
    assert validate(call, item).status_code == 201
    item = call('GET', '/deliverables/' + item['id']).json['data']
    assert item['validated'] and item['version'] == 2
    old = item['revisions'][0]
    revised = post(call, f"/deliverables/{item['id']}/revisions", {'expected_version': 2,
        'reference': 'opaque://new', 'artifact_identity': 'sha256:two'})
    assert revised.status_code == 201
    item = revised.json['data']
    assert not item['validated'] and item['revisions'][0] == old
    assert validate(call, item, revision_id=old['id'], artifact_identity=old['artifact_identity']).status_code == 201
    assert not call('GET', '/deliverables/' + item['id']).json['data']['validated']
    assert post(call, f"/tasks/{task['id']}/transition", {'expected_version': 1, 'state': 'failed'}).status_code == 200
    assert call('GET', '/deliverables?task_id=' + task['id']).json['data'][0]['revisions'] == item['revisions']
    assert call('GET', '/deliverable-revisions/' + old['id']).json['data'] == old
    assert artifact(call, {**task, 'version': 2}, 'failure-log')['id']
    assert call('GET', '/deliverables/' + item['id'], token='b').status_code == 403


def test_supersession_and_versions(env, make_task):
    call = env[2]
    task = make_task()
    a, b = artifact(call, task), artifact(call, task, 'replacement')
    other = artifact(call, make_task())
    def supersede(source, dest, v=1):
        return post(call, f"/deliverables/{source['id']}/supersede", {'expected_version': v, 'superseded_by': dest['id']})
    assert supersede(a, other).status_code == 409
    assert supersede(a, a).status_code == 409
    assert supersede(a, b, 9).status_code == 409
    assert supersede(a, b).status_code == 201
    assert supersede(b, a).status_code == 409
    assert post(call, f"/deliverables/{a['id']}/revisions", {'expected_version': 2,
        'reference': 'x', 'artifact_identity': 'x'}).status_code == 409


def test_checkpoint_known_good_restart_readonly(env, make_task):
    call = env[2]
    task = make_task()
    c = contract(call, task, {'required_deliverables': ['result']}).json['data']['contract']
    task = call('GET', '/tasks/' + task['id']).json['data']
    item = artifact(call, task)
    payload = {'expected_version': task['version'], 'revision_ids': [item['revisions'][0]['id']], 'blockers': ['review']}
    path = f"/tasks/{task['id']}/checkpoints"
    assert post(call, path, payload).status_code == 409
    assert post(call, path, {**payload, 'revision_ids': [str(uuid4())]}).status_code == 404
    assert validate(call, item).status_code == 201
    cp = post(call, path, payload, key='checkpoint')
    assert cp.status_code == 201, cp.json
    assert post(call, path, payload, key='checkpoint').json == cp.json
    cp = cp.json['data']
    assert cp['contract_ids'] == [c['id']] and cp['completed_requirements'] == ['result']
    assert cp['revisions'][0]['evidence_id']
    assert post(call, f"/tasks/{task['id']}/transition", {'expected_version': 2, 'state': 'failed'}).status_code == 200
    restarted = create_app(env[0])
    r = restarted.test_client().get('/api/v1/checkpoints/' + cp['id'], headers={'Authorization': 'Bearer ' + 'a'*32})
    assert r.json['data'] == cp
    restarted.extensions['hsm_engine'].dispose()
    assert call('GET', '/tasks/' + task['id']).json['data']['state'] == 'failed'
    assert call('POST', '/checkpoints/' + cp['id'] + '/restore', {}).status_code == 404


def test_checkpoint_cross_task_and_historical_evidence(env, make_task):
    call = env[2]
    t, other = make_task(), make_task()
    item = artifact(call, t)
    assert validate(call, item).status_code == 201
    payload = {'expected_version': 1, 'revision_ids': [item['revisions'][0]['id']]}
    assert post(call, f"/tasks/{other['id']}/checkpoints", payload).status_code == 409
    cp = post(call, f"/tasks/{t['id']}/checkpoints", payload).json['data']
    item = call('GET', '/deliverables/' + item['id']).json['data']
    assert validate(call, item, outcome='failed').status_code == 201
    assert post(call, f"/tasks/{t['id']}/checkpoints", payload).status_code == 409
    assert call('GET', '/checkpoints/' + cp['id']).json['data'] == cp


def test_completion_and_stop_gate_legacy_and_ancestor(env, make_task):
    call = env[2]
    task = make_task()
    assert contract(call, task, {'required_deliverables': ['result']}).status_code == 201
    task = call('GET', '/tasks/' + task['id']).json['data']
    for state in ['planning', 'running', 'review']:
        r = post(call, f"/tasks/{task['id']}/transition", {'expected_version': task['version'], 'state': state})
        assert r.status_code == 200, r.json
        task = r.json['data']
    assert post(call, f"/tasks/{task['id']}/transition", {'expected_version': task['version'], 'state': 'completed'}).json['error']['code'] == 'required_deliverables_missing'
    descendant = child(call, task).json['data']
    item = artifact(call, task)
    assert validate(call, item).status_code == 201
    assert child(call, task).json['error']['code'] == 'stop_when_satisfied'
    assert call('PATCH', '/tasks/' + descendant['id'], {'expected_version': 1, 'title': 'new work'}).json['error']['code'] == 'stop_when_satisfied'
    assert exception(call, task)['status'] == 'open'
    assert post(call, f"/tasks/{task['id']}/transition", {'expected_version': task['version'], 'state': 'completed'}).status_code == 200
    assert call('GET', '/tasks/' + descendant['id']).json['data']['state'] == 'queued'
    assert post(call, f"/tasks/{descendant['id']}/transition", {'expected_version': 1, 'state': 'cancelled'}).status_code == 200
    empty = make_task()
    assert contract(call, empty).status_code == 201
    assert child(call, empty).status_code == 201
    assert child(call, make_task()).status_code == 201


def test_remediation_lifecycle_atomic_replay_and_no_generic_bypass(env, make_task):
    call = env[2]
    task = make_task()
    assert contract(call, task, {'retry_budget': 1}).status_code == 201
    task = call('GET', '/tasks/' + task['id']).json['data']
    exc = exception(call, task)
    approval = grant(call, exc)
    r = associate(call, exc, approval)
    assert r.status_code == 201, r.json
    record = r.json['data']
    payload = {'expected_version': approval['version'], **exc['remediation_grant']}
    assert call('POST', f"/approvals/{approval['id']}/consume", payload, token='a').json['error']['code'] == 'remediation_required'
    result = authorize(call, record, approval, key='authorize')
    assert result.status_code == 201, result.json
    assert authorize(call, record, approval, key='authorize').json == result.json
    assert authorize(call, record, approval).status_code == 409
    assert call('GET', '/approvals/' + approval['id']).json['data']['consumed_by'] == 'worker-a'
    exc = call('GET', '/exceptions/' + exc['id']).json['data']
    new_grant = grant(call, exc)
    new_record = associate(call, exc, new_grant).json['data']
    assert authorize(call, new_record, new_grant).json['error']['code'] == 'retry_budget'
    assert call('GET', '/approvals/' + new_grant['id']).json['data']['consumed_at'] is None
    exc = call('GET', '/exceptions/' + exc['id']).json['data']
    r = post(call, f"/exceptions/{exc['id']}/resolve", {'expected_version': exc['version'], 'note': 'fixed'})
    assert r.status_code == 201
    assert r.json['data']['status'] == 'resolved'
    assert call('GET', '/tasks/' + task['id']).json['data']['state'] == 'queued'
    assert any(e['event_type'] == 'exception.resolved' for e in call('GET', f"/tasks/{task['id']}/events").json['data'])


def test_rejected_equivalent_new_identifiers_and_changed_text(env, make_task):
    call = env[2]
    task = make_task()
    exc = exception(call, task)
    first = grant(call, exc, decision=None)
    record = associate(call, exc, first).json['data']
    assert call('POST', f"/approvals/{first['id']}/resolve", {'expected_version': 1, 'decision': 'rejected'}).status_code == 200
    assert call('GET', '/remediations/' + record['id']).json['data']['status'] == 'rejected'
    fresh = grant(call, exc)
    exc = call('GET', '/exceptions/' + exc['id']).json['data']
    assert associate(call, exc, fresh).json['error']['code'] == 'remediation_rejected'
    duplicate = post(call, f"/tasks/{task['id']}/exceptions", {'expected_version': 1,
        'failure_kind': 'execution', 'description': 'entirely different prose'})
    assert duplicate.json['error']['code'] == 'exception_exists'
    assert call('GET', '/approvals/' + fresh['id']).json['data']['consumed_at'] is None


@pytest.mark.parametrize('change', ['mismatch', 'expiry', 'requester', 'amendment'])
def test_grant_mismatch_expiry_requester_contract_binding(env, make_task, change):
    call = env[2]
    task = make_task()
    exc = exception(call, task)
    approval = grant(call, exc, **({'artifact': 'wrong'} if change == 'mismatch' else {}))
    if change == 'mismatch':
        assert associate(call, exc, approval).status_code == 409
        return
    if change == 'requester':
        assert post(call, f"/exceptions/{exc['id']}/remediations", {'expected_version': 1, 'approval_id': approval['id']}).status_code == 403
        return
    record = associate(call, exc, approval).json['data']
    if change == 'expiry':
        with transaction(env[1].extensions['hsm_engine'], True) as db:
            db.get(Approval, approval['id']).expires_at = '2000-01-01T00:00:00Z'
    else:
        assert contract(call, task).status_code == 201
    assert authorize(call, record, approval).status_code == 409
    assert call('GET', '/approvals/' + approval['id']).json['data']['consumed_at'] is None
    assert call('GET', '/remediations/' + record['id']).json['data']['status'] == 'requested'


def test_concurrent_ancestor_retry_budget(env, make_task):
    call = env[2]
    root = make_task()
    assert contract(call, root, {'retry_budget': 1}).status_code == 201
    pairs = []
    for _ in range(2):
        task = child(call, root).json['data']
        exc = exception(call, task)
        approval = grant(call, exc)
        pairs.append((associate(call, exc, approval).json['data'], approval))
    def run(pair):
        rec, approval = pair
        with env[1].test_client() as client:
            return client.post('/api/v1/remediations/' + rec['id'] + '/authorize',
                json={'expected_version': 1, 'expected_approval_version': 2},
                headers={'Authorization': 'Bearer ' + 'a'*32, 'Idempotency-Key': str(uuid4())}).status_code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(run, pairs)) == [201, 409]
    with transaction(env[1].extensions['hsm_engine']) as db:
        assert db.scalar(select(func.count()).select_from(Approval).where(Approval.consumed_at.is_not(None))) == 1
        assert db.scalar(select(func.count()).select_from(Remediation).where(Remediation.status == 'authorized')) == 1


def test_approval_consumption_after_terminal_regression(env, make_task):
    call = env[2]
    task = make_task()
    exc = exception(call, task)
    approval = grant(call, exc, operation='external', action='test')
    assert post(call, f"/tasks/{task['id']}/transition", {'expected_version': 1, 'state': 'failed'}).status_code == 200
    payload = {k: approval[k] for k in ('task_id', 'operation', 'target', 'action', 'artifact')}
    result = call('POST', f"/approvals/{approval['id']}/consume", {'expected_version': 2, **payload}, token='a')
    assert result.json['error']['code'] == 'terminal_task'
    assert call('GET', '/approvals/' + approval['id']).json['data']['consumed_at'] is None


def test_scope_exception_and_governed_generic_grants(env, make_task):
    call = env[2]
    task = make_task()
    exc = exception(call, task, 'scope')
    approval = grant(call, exc)
    assert associate(call, exc, approval).json['error']['code'] == 'contract_amendment_required'
    assert contract(call, task, {'prohibited_operations': ['external']}).status_code == 201
    approval = grant(call, exc, operation='external')
    payload = {k: approval[k] for k in ('task_id', 'operation', 'target', 'action', 'artifact')}
    assert call('POST', f"/approvals/{approval['id']}/consume", {'expected_version': 2, **payload}, token='a').json['error']['code'] == 'prohibited_operation'
    assert call('GET', '/approvals/' + approval['id']).json['data']['consumed_at'] is None


def test_current_grants_checked_even_on_replay(env, make_task):
    call = env[2]
    exc = exception(call, make_task())
    approval = grant(call, exc)
    record = associate(call, exc, approval).json['data']
    assert authorize(call, record, approval, key='once').status_code == 201
    env[0].credentials[1]['actions'].remove('approvals:consume')
    assert authorize(call, record, approval, key='once').status_code == 403


def test_migration_0004_preserves_populated_rows(tmp_path):
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    from sqlalchemy import inspect
    root = Path(__file__).resolve().parents[1]
    url = f'sqlite:///{tmp_path}/preserved.db'
    cfg = Config(str(root / 'alembic.ini'))
    cfg.set_main_option('script_location', str(root / 'migrations'))
    cfg.set_main_option('sqlalchemy.url', url)
    command.upgrade(cfg, '0004')
    engine = engine_for(url)
    with transaction(engine, True) as db:
        db.execute(text("INSERT INTO projects VALUES ('alpha', 'Alpha', '{}', 1, '2026-01-01')"))
        db.execute(text("INSERT INTO tasks (id, project_id, task_key, title, objective, state, created_at, updated_at, version) VALUES ('task', 'alpha', 'task', 'Old', 'Old', 'failed', '2026-01-01', '2026-01-01', 3)"))
        db.execute(text("INSERT INTO task_contracts VALUES ('contract', 'task', 1, 1, 0, '{}', 'operator', 'reason', '2026-01-01', '2026-01-01')"))
        db.execute(text("INSERT INTO incidents VALUES ('incident', 'alpha', 'missing_pane', 'pane', '2026-01-01', '2026-01-01', 'open', NULL)"))
        db.execute(text("INSERT INTO approvals (id, project_id, task_id, requester, operation, target, action, artifact, status, requested_at, expires_at, version) VALUES ('approval', 'alpha', 'task', 'worker', 'external', 'target', 'action', 'artifact', 'rejected', '2026-01-01', '2026-01-02', 2)"))
        db.execute(text("INSERT INTO events (id, project_id, task_id, timestamp, event_type, actor, details) VALUES ('event', 'alpha', 'task', '2026-01-01', 'task.failed', 'worker', '{}')"))
    inspector = inspect(engine)
    columns = {table: [c['name'] for c in inspector.get_columns(table)] for table in inspector.get_table_names() if table != 'alembic_version'}
    def snapshot():
        with engine.connect() as conn:
            return {table: conn.execute(text('SELECT ' + ', '.join('"' + c + '"' for c in cols) + ' FROM "' + table + '"')).all() for table, cols in columns.items()}
    before = snapshot()
    migrate(url)
    migrate(url)
    assert snapshot() == before
    with transaction(engine) as db:
        assert db.execute(text('PRAGMA foreign_key_check')).all() == []
        assert db.execute(text('PRAGMA integrity_check')).scalar() == 'ok'
        item = db.get(Incident, 'incident')
        assert item is not None
        assert item.version == 1 and item.task_id is None
        assert item.evidence_reference is None and item.proposed_action is None and item.scope_impact is None
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar() == '0006'
        assert any(row[2] == 'tasks' for row in db.execute(text('PRAGMA foreign_key_list(incidents)')))
    engine.dispose()


def test_inspection_idempotency_and_terminal_remediation(env, make_task):
    call = env[2]
    task = make_task()
    exc = exception(call, task)
    data = {'expected_version': 1}
    r = post(call, f"/exceptions/{exc['id']}/inspect", data, key='inspection')
    assert r.status_code == 201
    assert post(call, f"/exceptions/{exc['id']}/inspect", data, key='inspection').json == r.json
    assert post(call, f"/exceptions/{exc['id']}/inspect", data).status_code == 409
    exc = r.json['data']
    approval = grant(call, exc)
    record = associate(call, exc, approval).json['data']
    assert post(call, f"/tasks/{task['id']}/transition", {'expected_version': 1, 'state': 'failed'}).status_code == 200
    assert authorize(call, record, approval).json['error']['code'] == 'terminal_task'
    assert call('GET', '/approvals/' + approval['id']).json['data']['consumed_at'] is None
    assert len(call('GET', '/exceptions?status=open&task_id=' + task['id']).json['data']) == 1
    events = call('GET', f"/tasks/{task['id']}/events").json['data']
    assert sum(e['event_type'] == 'exception.inspected' for e in events) == 1


def test_remediation_consumption_rollback_after_flushed_mutation(env, make_task, monkeypatch):
    from hsm import approvals
    from hsm.governance import deny
    call = env[2]
    task = make_task()
    exc = exception(call, task)
    approval = grant(call, exc)
    record = associate(call, exc, approval).json['data']
    real = approvals.consume
    def fail(service, ident, data, **kw):
        real(service, ident, data, **kw)
        service.db.flush()
        deny(service.db.get(Task, task['id']), 'injected_policy_denial')
    monkeypatch.setattr(approvals, 'consume', fail)
    assert authorize(call, record, approval).status_code == 409
    assert call('GET', '/approvals/' + approval['id']).json['data']['consumed_at'] is None
    assert call('GET', '/remediations/' + record['id']).json['data']['status'] == 'requested'
    events = call('GET', f"/tasks/{task['id']}/events").json['data']
    assert not any(e['event_type'] == 'approval.consumed' for e in events)
    assert any(e['event_type'] == 'governance.denied' for e in events)
