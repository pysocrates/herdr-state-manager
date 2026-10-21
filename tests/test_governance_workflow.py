"""Cross-feature lifecycle through authenticated routes on a temporary database.

HSM authorizes and records work; neither retry authorization nor recovery executes it.
"""
from hsm.app import create_app
from test_governance import contract, child
from test_durable import artifact, validate, exception, grant, associate, authorize, post
from test_interventions import intervene
from test_recovery_governance import checkpoint, dump, read, transition


def data(response, status=200):
    assert response.status_code == status, response.json
    return response.json['data']


def test_governed_work_survives_pause_failure_and_replacement(env, make_task):
    cfg, app, operator = env

    def agent(method, path, payload=None, **kwargs):
        kwargs.setdefault('token', 'a')
        return operator(method, path, payload, **kwargs)

    def get(path):
        return data(agent('GET', path))

    def fresh(task):
        return get('/tasks/' + task['id'])

    def metrics():
        return get('/governance/metrics?project=alpha')

    # Operator establishes bounded authority; actual delegation uses agent credentials.
    owner = data(agent('POST', '/agent-sessions',
                       {'project_id': 'alpha', 'profile': 'original-worker'}), 201)
    root = make_task()
    root_spec = {'max_children': 1, 'max_descendant_depth': 1,
                 'resource_labels': ['repo'], 'retry_budget': 1,
                 'max_retries_per_failure': 1}
    root_contract = data(contract(operator, root, root_spec), 201)['contract']
    root = fresh(root)
    worker = data(child(agent, root, resource_labels=['repo'],
                        owner_session_id=owner['id']), 201)
    provenance = worker['delegation_history'][0]
    assert provenance['parent_contract_id'] == root_contract['id']
    assert provenance['authorized_by'] == 'worker-a'
    local_spec = {'required_deliverables': ['result'], 'stop_when_satisfied': False}
    local_contract = data(contract(operator, worker, local_spec), 201)['contract']
    worker = fresh(worker)
    for state in ['planning', 'running']:
        worker = transition(agent, worker, state)

    # Validated durable work is independent of the worker's eventual success.
    item = artifact(agent, worker)
    data(validate(agent, item), 201)
    item = get('/deliverables/' + item['id'])
    original_revision = item['revisions'][0]
    cp = checkpoint(agent, worker, item, 'review')
    assert cp['contract_ids'] == [local_contract['id'], root_contract['id']]
    assert cp['completed_requirements'] == ['result']
    assert cp['revisions'][0]['revision_id'] == original_revision['id']
    assert cp['revisions'][0]['evidence_id']
    assert read(agent, worker)['next_authorized_action'] == 'review'

    exc = exception(agent, worker)
    approval = grant(operator, exc)  # agent requests; operator resolves
    remediation = data(associate(agent, exc, approval), 201)
    before_authorization = fresh(worker)
    authorized = data(authorize(agent, remediation, approval, key='workflow-retry'), 201)
    assert authorized['status'] == 'authorized'
    assert fresh(worker) == before_authorization  # not execution or a transition
    assert get('/approvals/' + approval['id'])['consumed_by'] == 'worker-a'
    assert read(agent, worker)['remaining_budget']['remaining_retries'] == 0
    before_replay = metrics()
    assert data(authorize(agent, remediation, approval, key='workflow-retry'), 201) == authorized
    after_replay = metrics()
    assert after_replay['counts'] == before_replay['counts']
    assert after_replay['event_cursor'] == before_replay['event_cursor']
    assert after_replay['counts']['authorized_retries'] == 1

    # An ancestor pause gates the child's mediated work, not artifact/recovery reads.
    paused = data(intervene(operator, root))
    assert paused['state'] == root['state']
    denial_payload = {'expected_version': worker['version'], 'state': 'blocked'}
    denial_path = '/tasks/' + worker['id'] + '/transition'
    denied = post(agent, denial_path, denial_payload, key='workflow-paused-denial')
    assert denied.status_code == 409, denied.json
    assert denied.json['error']['code'] == 'governance_paused'
    denied_metrics = metrics()
    assert post(agent, denial_path, denial_payload, key='workflow-paused-denial').json == denied.json
    assert metrics()['counts'] == denied_metrics['counts']
    assert metrics()['event_cursor'] == denied_metrics['event_cursor']
    persisted = dump(app.extensions['hsm_engine'])
    paused_view = read(agent, worker)
    assert paused_view['task']['state'] == 'running'
    assert not paused_view['task']['governance']['paused']
    assert paused_view['task']['governance']['effective_paused']
    assert paused_view['next_authorized_action'] is None
    assert paused_view['next_action_reason'] == 'governance_paused'
    assert paused_view['latest_checkpoint'] == cp
    assert paused_view['deliverables'] == [item]
    assert paused_view['unresolved_exceptions'][0]['id'] == exc['id']
    assert paused_view['runtime_control'] == {'supported': False, 'acknowledged': False}
    assert dump(app.extensions['hsm_engine']) == persisted
    resumed = data(intervene(operator, paused, 'resume'))
    assert resumed['state'] == root['state']
    assert not fresh(worker)['governance']['effective_paused']
    assert read(agent, worker)['next_authorized_action'] == 'review'
    assert read(agent, worker)['remaining_budget']['remaining_retries'] == 0

    # Replace the terminated session before safe closure: terminal tasks are immutable.
    data(agent('PATCH', '/agent-sessions/' + owner['id'], {'runtime_status': 'terminated'}))
    replacement = data(agent('POST', '/agent-sessions',
                            {'project_id': 'alpha', 'profile': 'replacement-worker'}), 201)
    worker = data(agent('PATCH', '/tasks/' + worker['id'],
                        {'expected_version': worker['version'],
                         'owner_session_id': replacement['id']}))
    worker = transition(agent, worker, 'failed')
    failed_view = read(agent, worker)
    assert failed_view['task']['state'] == 'failed'
    assert failed_view['ownership']['owner_session_id'] == replacement['id']
    assert failed_view['latest_checkpoint'] == cp
    assert failed_view['deliverables'] == [item]
    assert failed_view['next_authorized_action'] is None
    assert failed_view['next_action_reason'] == 'terminal_task'

    # New app/engine/client recovers persisted history; GETs change no database rows.
    persisted = dump(app.extensions['hsm_engine'])
    restarted = create_app(cfg)
    try:
        with restarted.test_client() as client:
            recovered = data(client.get('/api/v1/tasks/' + worker['id'] + '/recovery',
                             headers={'Authorization': 'Bearer ' + 'a' * 32}))
        for key in ['task', 'ownership', 'deliverables', 'checkpoints', 'contracts',
                    'unresolved_exceptions', 'event_cursor', 'next_action_reason']:
            assert recovered[key] == failed_view[key]
    finally:
        restarted.extensions['hsm_engine'].dispose()
    assert dump(app.extensions['hsm_engine']) == persisted
    assert get('/deliverable-revisions/' + original_revision['id']) == original_revision
    assert get('/checkpoints/' + cp['id']) == cp

    # A separate task completes only after its CURRENT required output is validated.
    success = make_task(owner_session_id=replacement['id'])
    success_contract = data(contract(operator, success,
                            {'required_deliverables': ['result']}), 201)['contract']
    success = fresh(success)
    for state in ['planning', 'running', 'review']:
        success = transition(agent, success, state)
    output = artifact(agent, success)
    data(validate(agent, output), 201)
    output = get('/deliverables/' + output['id'])
    old_revision = output['revisions'][0]
    historical_cp = checkpoint(agent, success, output, 'complete')
    assert historical_cp['contract_ids'] == [success_contract['id']]
    output = data(post(agent, '/deliverables/' + output['id'] + '/revisions',
                       {'expected_version': output['version'], 'reference': 'opaque://replacement',
                        'artifact_identity': 'sha256:replacement'}), 201)
    assert not output['validated']
    stale = read(agent, success)
    assert stale['next_action_reason'] == 'required_deliverables_missing'
    assert stale['readiness']['missing_required_keys'] == ['result']
    missing = post(agent, '/tasks/' + success['id'] + '/transition',
                   {'expected_version': success['version'], 'state': 'completed'})
    assert missing.status_code == 409, missing.json
    assert missing.json['error']['code'] == 'required_deliverables_missing'
    data(validate(agent, output), 201)
    success = transition(agent, success, 'completed')
    assert read(agent, success)['readiness']['task_completed']
    assert get('/checkpoints/' + historical_cp['id']) == historical_cp
    assert get('/deliverable-revisions/' + old_revision['id']) == old_revision

    # Amend current ancestry without retroactively changing delegation/checkpoint evidence.
    amended = data(contract(operator, fresh(root), {**root_spec, 'max_children': 2},
                            version=1), 201)['contract']
    assert amended['id'] != root_contract['id']
    assert fresh(worker)['delegation_history'][0] == provenance
    assert get('/checkpoints/' + cp['id']) == cp
    history = get('/tasks/' + root['id'] + '/contract')
    assert history['history'][0] == root_contract
    events = get('/tasks/' + worker['id'] + '/events')
    created = next(e for e in events if e['event_type'] == 'task.created')
    assert created['actor'] == 'worker-a'
    assert created['details']['governing_contract_ids'] == [root_contract['id']]
    checkpoint_event = next(e for e in events if e['event_type'] == 'checkpoint.created')
    assert checkpoint_event['details']['governing_contract_ids'] == cp['contract_ids']

    final_metrics = metrics()
    expected_counts = {'tasks': 3, 'completed_tasks': 1, 'terminal_tasks': 2,
        'child_creation_events': 1, 'delegation_records_including_reparent': 1,
        'governance_denials': 2, 'delegation_denials_observed': 0,
        'contract_initializations': 3, 'amendment_successes': 1,
        'canonical_exceptions': 1, 'unresolved_exceptions': 1,
        'exception_recorded_events': 1, 'remediation_attempt_records': 1,
        'authorized_retries': 1, 'deliverables': 2, 'validated_deliverables': 2,
        'tasks_with_requirements': 2, 'tasks_with_requirements_satisfied': 2}
    assert {k: final_metrics['counts'][k] for k in expected_counts} == expected_counts
    assert final_metrics['provenance']['historical_child_creations_by_root_at_creation'] == [
        {'root_id': root['id'], 'count': 1}]
    snapshot = get('/state?project=alpha&recent_events=1')
    assert len(snapshot['recent_events']) == 1
    assert snapshot['governance']['metrics']['counts'] == final_metrics['counts']
    assert {t['id'] for t in snapshot['active_tasks']} == {root['id']}
    assert read(agent, worker)['task']['state'] == 'failed'
    assert get('/deliverables/' + item['id']) == item
