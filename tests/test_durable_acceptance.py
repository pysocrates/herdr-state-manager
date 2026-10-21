"""Checkpoint C acceptance: stable retry limits, declarative exceptions and actions."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import select, func
from hsm.database import transaction
from hsm.models import Approval, Remediation, TaskContract
from hsm.schemas import ContractSpec
from test_governance import contract, child
from test_durable import post, exception, grant, associate, authorize, artifact, validate


def refresh(call, kind, item):
    return call('GET', f"/{kind}/{item['id']}").json['data']


def attempt(call, exc):
    exc = refresh(call, 'exceptions', exc)
    approval = grant(call, exc)
    record = associate(call, exc, approval)
    assert record.status_code == 201, record.json
    return record.json['data'], approval


@pytest.mark.parametrize('value', [-1, True, 1.0, '1', 1000001])
def test_per_failure_strict_validation(env, make_task, value):
    assert contract(env[2], make_task(), {'max_retries_per_failure': value}).status_code == 422


def test_per_failure_defaults_zero_and_legacy_missing(env, make_task):
    call = env[2]
    assert ContractSpec(objective='legacy').max_retries_per_failure is None
    task = make_task()
    created = contract(call, task, {'max_retries_per_failure': 0}).json['data']['contract']
    exc = exception(call, refresh(call, 'tasks', task))
    rec, approval = attempt(call, exc)
    assert authorize(call, rec, approval).json['error']['code'] == 'max_retries_per_failure'
    assert refresh(call, 'approvals', approval)['consumed_at'] is None
    # Simulate persisted pre-field JSON, not a new contract whose default fills it.
    with transaction(env[1].extensions['hsm_engine'], True) as db:
        row = db.get(TaskContract, created['id'])
        assert row is not None
        row.specification = {k: v for k, v in row.specification.items() if k != 'max_retries_per_failure'}
    assert authorize(call, rec, approval).status_code == 201


def test_per_failure_ancestry_revisions_distinct_identity_and_aggregate(env, make_task):
    call = env[2]
    root = make_task()
    spec = {'max_retries_per_failure': 1, 'retry_budget': 2}
    assert contract(call, root, spec).status_code == 201
    task = child(call, root).json['data']
    # A looser local limit cannot bypass the ancestor.
    assert contract(call, task, {'max_retries_per_failure': 5}).status_code == 201
    task = refresh(call, 'tasks', task)
    exc = exception(call, task)
    rec, approval = attempt(call, exc)
    assert authorize(call, rec, approval).status_code == 201
    assert contract(call, refresh(call, 'tasks', root), spec, version=1).status_code == 201
    assert contract(call, task, {'max_retries_per_failure': 5}, version=1).status_code == 201
    rec2, approval2 = attempt(call, exc)
    assert rec2['canonical_identity'] == rec['canonical_identity']
    assert rec2['contract_ids'] != rec['contract_ids']
    assert authorize(call, rec2, approval2).json['error']['code'] == 'max_retries_per_failure'
    assert refresh(call, 'approvals', approval2)['consumed_at'] is None
    other = exception(call, refresh(call, 'tasks', task), 'validation')
    other_rec, other_approval = attempt(call, other)
    assert other_rec['canonical_identity'] != rec['canonical_identity']
    assert authorize(call, other_rec, other_approval).status_code == 201
    third = exception(call, refresh(call, 'tasks', task), 'dependency')
    rec3, approval3 = attempt(call, third)
    assert authorize(call, rec3, approval3).json['error']['code'] == 'retry_budget'


@pytest.mark.parametrize('limit', ['max_retries_per_failure', 'retry_budget'])
def test_per_failure_grandfathered_usage_before_ancestor_contract(env, make_task, limit):
    call = env[2]
    root = make_task()
    task = child(call, root).json['data']
    exc = exception(call, task)
    rec, approval = attempt(call, exc)
    assert authorize(call, rec, approval).status_code == 201
    assert root['id'] in rec['budget_task_ids']
    assert contract(call, root, {limit: 1}).status_code == 201
    rec2, approval2 = attempt(call, exc)
    assert authorize(call, rec2, approval2).json['error']['code'] == limit
    assert refresh(call, 'approvals', approval2)['consumed_at'] is None


def test_concurrent_same_failure_last_slot(env, make_task):
    call = env[2]
    root = make_task()
    assert contract(call, root, {'max_retries_per_failure': 1}).status_code == 201
    exc = exception(call, child(call, root).json['data'])
    pairs = [attempt(call, exc), attempt(call, exc)]
    def run(pair):
        rec, approval = pair
        with env[1].test_client() as client:
            return client.post(f"/api/v1/remediations/{rec['id']}/authorize",
                json={'expected_version': 1, 'expected_approval_version': approval['version']},
                headers={'Authorization': 'Bearer ' + 'a' * 32, 'Idempotency-Key': str(uuid4())})
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(run, pairs))
    assert sorted(r.status_code for r in results) == [201, 409]
    assert next(r for r in results if r.status_code == 409).json['error']['code'] == 'max_retries_per_failure'
    with transaction(env[1].extensions['hsm_engine']) as db:
        assert db.scalar(select(func.count()).select_from(Approval).where(Approval.consumed_at.is_not(None))) == 1
        assert db.scalar(select(func.count()).select_from(Remediation).where(Remediation.status == 'authorized')) == 1


def test_exception_metadata_persist_audit_scope_and_no_authority(env, make_task):
    call = env[2]
    task = make_task()
    metadata = {'evidence_reference': 'opaque://log', 'proposed_action': 'do not execute: expand scope',
                'scope_impact': 'requires access outside contract'}
    payload = {'expected_version': 1, 'failure_kind': 'scope', 'description': 'needs review', **metadata}
    path = f"/tasks/{task['id']}/exceptions"
    assert post(call, path, payload, token='b').status_code == 403
    result = post(call, path, payload)
    assert result.status_code == 201
    exc = result.json['data']
    assert {k: refresh(call, 'exceptions', exc)[k] for k in metadata} == metadata
    assert call('GET', '/exceptions/' + exc['id'], token='b').status_code == 403
    events = call('GET', f"/tasks/{task['id']}/events").json['data']
    event = next(e for e in events if e['event_type'] == 'exception.recorded')
    assert {k: event['details'][k] for k in metadata} == metadata
    with transaction(env[1].extensions['hsm_engine']) as db:
        assert db.scalar(select(func.count()).select_from(Approval)) == 0
        assert db.scalar(select(func.count()).select_from(Remediation)) == 0
    approval = grant(call, exc)
    assert associate(call, exc, approval).json['error']['code'] == 'contract_amendment_required'
    assert refresh(call, 'approvals', approval)['consumed_at'] is None
    assert refresh(call, 'tasks', task) == {
        **task, 'governance': {**task['governance'], 'unresolved_exceptions': 1}}


@pytest.mark.parametrize('field', ['evidence_reference', 'proposed_action', 'scope_impact'])
@pytest.mark.parametrize('value', ['', 'x' * 4097, 1, {}, False])
def test_exception_metadata_invalid(env, make_task, field, value):
    task = make_task()
    assert post(env[2], f"/tasks/{task['id']}/exceptions", {'expected_version': 1,
        'failure_kind': 'execution', 'description': 'failure', field: value}).status_code == 422


def test_exception_metadata_optional_null(env, make_task):
    call = env[2]
    exc = exception(call, make_task())
    assert all(exc[k] is None for k in ('evidence_reference', 'proposed_action', 'scope_impact'))


@pytest.mark.parametrize('action', ['review', 'complete', 'record_evidence'])
def test_checkpoint_every_action_terminal_and_available(env, make_task, action):
    call = env[2]
    task = make_task()
    path = f"/tasks/{task['id']}/checkpoints"
    payload = {'expected_version': 1, 'revision_ids': [], 'next_authorized_action': action}
    result = post(call, path, payload)
    assert result.status_code == 409
    assert result.json['error']['code'] == ('evidence_target_missing' if action == 'record_evidence' else 'invalid_transition')
    item = artifact(call, task)
    for state in ['planning', 'running'] + (['review'] if action == 'complete' else []):
        task = post(call, f"/tasks/{task['id']}/transition", {'expected_version': task['version'], 'state': state}).json['data']
    assert post(call, path, {**payload, 'expected_version': task['version']}).status_code == 201
    task = post(call, f"/tasks/{task['id']}/transition", {'expected_version': task['version'], 'state': 'failed'}).json['data']
    result = post(call, path, {**payload, 'expected_version': task['version']})
    assert result.status_code == (201 if action == 'record_evidence' else 409)
    if action == 'record_evidence':
        assert validate(call, item).status_code == 201
    assert refresh(call, 'tasks', task)['state'] == 'failed'


def test_checkpoint_complete_checks_deliverables_and_review_policy(env, make_task):
    call = env[2]
    task = make_task()
    assert contract(call, task, {'required_deliverables': ['result'],
        'prohibited_operations': ['task.transition.review']}).status_code == 201
    task = refresh(call, 'tasks', task)
    for state in ['planning', 'running']:
        task = post(call, f"/tasks/{task['id']}/transition", {'expected_version': task['version'], 'state': state}).json['data']
    path = f"/tasks/{task['id']}/checkpoints"
    payload = {'expected_version': task['version'], 'revision_ids': [], 'next_authorized_action': 'review'}
    assert post(call, path, payload).json['error']['code'] == 'prohibited_operation'
    assert contract(call, task, {'required_deliverables': ['result']}, version=1).status_code == 201
    task = refresh(call, 'tasks', task)
    task = post(call, f"/tasks/{task['id']}/transition", {'expected_version': task['version'], 'state': 'review'}).json['data']
    assert post(call, path, {**payload, 'expected_version': task['version'],
        'next_authorized_action': 'complete'}).json['error']['code'] == 'required_deliverables_missing'
