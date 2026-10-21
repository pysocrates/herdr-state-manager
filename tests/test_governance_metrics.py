from datetime import datetime, timedelta, timezone
from hsm.database import transaction
from hsm.models import Event, Incident
from hsm import reporting
from test_governance import contract, child
from test_durable import post, artifact, validate, exception, grant, associate, authorize
from test_recovery_governance import transition, dump


def metrics(call, suffix='?project=alpha', token='a'):
    response = call('GET', '/governance/metrics' + suffix, token=token)
    assert response.status_code == 200, response.json
    return response.json['data']


def test_full_history_not_recent_events_replay_exclusion_and_denial_metadata(env, make_task):
    call = env[2]
    root = make_task()
    assert contract(call, root, {'max_children': 0}).status_code == 201
    denied_payload = {'project_id': 'alpha', 'task_key': 'denied', 'title': 'child',
        'objective': 'work', 'parent_id': root['id'], 'spawn_reason': 'explicit'}
    for key in ['one', 'two', 'three']:
        first = post(call, '/tasks', denied_payload, key=key)
        assert first.status_code == 409
        assert post(call, '/tasks', denied_payload, key=key).json == first.json
        if key == 'two':
            assert not any(a['code'] == 'repeated_denied_expansion' for a in metrics(call)['alerts'])
    # More than the maximum recent_events page: all are persisted, not a fake response.
    with transaction(env[1].extensions['hsm_engine'], True) as db:
        for index in range(205):
            db.add(Event(project_id='alpha', task_id=root['id'], event_type='task.updated',
                         actor='fixture', details={'fixture': index}))
    snap = call('GET', '/state?project=alpha&recent_events=1').json['data']
    assert len(snap['recent_events']) == 1 and snap['truncation']['recent_events']
    view = metrics(call)
    assert view['counts']['governance_denials'] == view['counts']['delegation_denials_observed'] == 3
    assert view['counts']['unclassified_legacy_denials'] == 0
    assert snap['governance']['metrics']['counts'] == view['counts']
    assert any(a['code'] == 'repeated_denied_expansion' for a in view['alerts'])
    denials = call('GET', '/events?project=alpha&offset=205&limit=10').json['data']
    denials = [e for e in denials if e['event_type'] == 'governance.denied']
    assert len(denials) == 3
    assert all(e['details']['request_type'] == 'delegation.create' for e in denials)


def test_historical_creation_root_is_not_mutable_current_root(env, make_task):
    call = env[2]
    root, other = make_task(), make_task()
    branch = make_task(parent_id=root['id'])
    leaf = make_task(parent_id=branch['id'])
    assert call('PATCH', '/tasks/' + branch['id'], {'expected_version': 1, 'parent_id': other['id']}).status_code == 200
    new_leaf = make_task(parent_id=branch['id'])
    view = metrics(call)
    provenance = view['provenance']
    historical = {row['root_id']: row['count'] for row in provenance['historical_child_creations_by_root_at_creation']}
    assert historical == {root['id']: 2, other['id']: 1}
    assert provenance['current_children_by_current_root'] == [{'root_id': other['id'], 'count': 3}]
    assert view['counts']['child_creation_events'] == 3
    assert view['counts']['delegation_records_including_reparent'] == 4
    # Simulate legacy imported root without immutable historical ancestry evidence.
    with transaction(env[1].extensions['hsm_engine'], True) as db:
        db.query(Event).filter(Event.task_id == root['id']).delete()
    view = metrics(call)
    assert view['provenance']['historical_root_unavailable_count'] == 2
    assert view['provenance']['tasks_without_creation_event_count'] == 1
    assert view['provenance']['historical_child_creations_by_root_at_creation'] == [{'root_id': other['id'], 'count': 1}]
    assert view['coverage']['original_root_without_recorded_ancestry']['value'] is None


def test_amendments_and_old_denials_are_partial_not_fabricated(env, make_task):
    call = env[2]
    root = make_task()
    assert contract(call, root, {}).status_code == 201
    root = call('GET', '/tasks/' + root['id']).json['data']
    assert child(call, root).status_code == 201
    payload = {'expected_version': root['version'], 'expected_contract_version': 1,
               'reason': 'cannot erase consumed child', 'contract': {'objective': 'bounded', 'max_children': 0}}
    for key in ['deny-amend', 'deny-amend']:
        assert post(call, '/tasks/' + root['id'] + '/contract', payload, key=key).status_code == 409
    assert contract(call, root, {'max_children': 2}, version=1, key='success').status_code == 201
    with transaction(env[1].extensions['hsm_engine'], True) as db:
        old = Event(project_id='alpha', task_id=root['id'], event_type='governance.denied',
                    actor='legacy', details={'code': 'child_budget'})
        db.add(old)
    before = dump(env[1].extensions['hsm_engine'])
    view = metrics(call)
    assert view['counts']['contract_initializations'] == 1
    assert view['counts']['amendment_successes'] == 1
    assert view['counts']['amendment_requests_observed'] == 2
    assert view['counts']['unclassified_legacy_denials'] == 1
    assert view['counts']['delegation_denials_observed'] == 0  # old code alone is ambiguous
    assert dump(env[1].extensions['hsm_engine']) == before


def test_retry_attempts_readiness_unknown_telemetry_and_canonical_not_recurrence(env, make_task):
    call = env[2]
    task = make_task()
    assert contract(call, task, {'retry_budget': 3, 'max_retries_per_failure': 3,
        'required_deliverables': ['result'], 'stop_when_satisfied': False}).status_code == 201
    task = call('GET', '/tasks/' + task['id']).json['data']
    exc = exception(call, task)
    for index in range(3):
        exc = call('GET', '/exceptions/' + exc['id']).json['data']
        approval = grant(call, exc)
        record = associate(call, exc, approval).json['data']
        assert authorize(call, record, approval, key='retry-' + str(index)).status_code == 201
        assert authorize(call, record, approval, key='retry-' + str(index)).status_code == 201
    exc = call('GET', '/exceptions/' + exc['id']).json['data']
    assert post(call, '/exceptions/' + exc['id'] + '/inspect', {'expected_version': exc['version']}).status_code == 201
    # Duplicate canonical recording is not a new observed occurrence.
    assert post(call, '/tasks/' + task['id'] + '/exceptions', {'expected_version': task['version'],
        'failure_kind': 'execution', 'description': 'again'}).status_code == 409
    item = artifact(call, task)
    assert validate(call, item).status_code == 201
    view = metrics(call)
    counts = view['counts']
    assert counts['remediation_attempt_records'] == counts['authorized_retries'] == 3
    assert counts['canonical_exceptions'] == counts['exception_recorded_events'] == 1
    assert counts['exception_inspections'] == 1
    assert view['coverage']['exception_recurrence']['status'] == 'unavailable'
    assert counts['validated_deliverables'] == counts['tasks_with_requirements_satisfied'] == 1
    assert counts['completed_tasks'] == 0
    assert view['runtime']['context_utilization']['value'] is None
    assert view['budgets'][0]['remaining_retries'] == 0
    assert view['budgets'][0]['constraints'][0]['used']['authorized_retries'] == 3
    assert any(a['code'] == 'repeated_authorized_retries' for a in view['alerts'])


def test_deterministic_threshold_boundaries_stalled_terminal_closure_and_sort(env, make_task, monkeypatch):
    call = env[2]
    root = make_task()
    assert contract(call, root, {'max_descendant_depth': 2, 'required_deliverables': ['missing']}).status_code == 201
    root = call('GET', '/tasks/' + root['id']).json['data']
    leaf = child(call, root).json['data']
    exceptions = [exception(call, root, kind) for kind in ['execution', 'validation', 'dependency']]
    root = transition(call, root, 'failed')
    fixed = datetime(2027, 1, 1, tzinfo=timezone.utc)
    with transaction(env[1].extensions['hsm_engine'], True) as db:
        for exc in exceptions:
            row = db.get(Incident, exc['id'])
            assert row is not None
            row.last_seen_at = (fixed - timedelta(seconds=3600)).isoformat()
    monkeypatch.setattr(reporting, 'now', lambda: (fixed - timedelta(seconds=1)).isoformat())
    assert metrics(call)['stalled_task_ids'] == []
    monkeypatch.setattr(reporting, 'now', lambda: fixed.isoformat())
    before = dump(env[1].extensions['hsm_engine'])
    view = metrics(call)
    assert view == metrics(call)
    assert view['stalled_task_ids'] == [root['id']]
    assert view['orphan_task_ids'] == [leaf['id']]
    assert view['alerts'] == sorted(view['alerts'], key=lambda a: (a['task_id'], a['code']))
    codes = {(a['task_id'], a['code']) for a in view['alerts']}
    assert (leaf['id'], 'depth_limit_approached') in codes
    assert (root['id'], 'depth_limit_approached') not in codes
    for code in ['closure_missing_required_outputs', 'multiple_canonical_exceptions', 'stalled_unresolved_exceptions']:
        assert (root['id'], code) in codes
    assert dump(env[1].extensions['hsm_engine']) == before


def test_project_task_auth_and_ended_owner_orphan(env, make_task):
    call = env[2]
    owner = call('POST', '/agent-sessions', {'project_id': 'alpha', 'profile': 'worker'}).json['data']
    task = make_task(owner_session_id=owner['id'])
    beta = make_task(project='beta')
    assert not metrics(call)['orphan_task_ids']
    assert call('PATCH', '/agent-sessions/' + owner['id'], {'runtime_status': 'terminated'}).status_code == 200
    assert metrics(call)['orphan_task_ids'] == [task['id']]
    assert metrics(call, '')['project_ids'] == ['alpha']
    assert metrics(call, '', token='o')['project_ids'] == ['alpha', 'beta']
    detail = call('GET', '/tasks/' + task['id'] + '/metrics', token='a').json['data']
    assert detail['selection'] == 'exact_task' and detail['counts']['tasks'] == 1
    assert call('GET', '/tasks/' + beta['id'] + '/metrics', token='a').status_code == 403
    assert call('GET', '/governance/metrics?project=beta', token='a').status_code == 403
    assert call('GET', '/governance/metrics', token='x').status_code == 401
    assert call('GET', '/governance/metrics?project=alpha&project=beta').status_code == 422
    assert call('GET', '/tasks/' + task['id'] + '/metrics?project=alpha').status_code == 422
