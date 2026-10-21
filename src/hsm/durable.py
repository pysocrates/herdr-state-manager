"""Durable metadata and remediation policy within Service's serialized transaction.

References are never opened. Authorization records are not execution or resumption.
"""
import hashlib
import json
from sqlalchemy import select
from .models import (Task, Deliverable, DeliverableRevision, ValidationEvidence, Checkpoint,
                     CheckpointRevision, Incident, Approval, Remediation, now)
from .services import Problem, serialize
from .governance import ancestry, current, deny, check_operation, check_window, check_scope
from .state_machine import ACTIVE, TRANSITIONS


def version(item, data):
    if item.version != data['expected_version']:
        raise Problem(409, 'stale_version', 'Resource version has changed')


def latest(s, item):
    return s.db.scalar(select(DeliverableRevision).where(DeliverableRevision.deliverable_id == item.id)
                       .order_by(DeliverableRevision.revision.desc()).limit(1))


def evidence(s, revision):
    return s.db.scalar(select(ValidationEvidence).where(ValidationEvidence.revision_id == revision.id)
                       .order_by(ValidationEvidence.created_at.desc(), ValidationEvidence.id.desc()).limit(1))


def good(s, revision):
    ev = evidence(s, revision)
    return ev if ev and ev.outcome == 'passed' and ev.artifact_identity == revision.artifact_identity else None


def satisfied(s, task, revision):
    required = set(revision.specification.get('required_deliverables', []))
    return bool(required) and required <= completed(s, task.id)


def completed(s, task_id):
    result = set()
    for item in s.db.scalars(select(Deliverable).where(Deliverable.task_id == task_id, Deliverable.superseded_by.is_(None))):
        rev = latest(s, item)
        if rev and good(s, rev):
            result.add(item.key)
    return result


def check_complete(s, task):
    rev = current(s, task.id)
    if rev and not set(rev.specification.get('required_deliverables', [])) <= completed(s, task.id):
        deny(task, 'required_deliverables_missing')


def check_work(s, task, operation):
    for owner, rev in ancestry(s, task):
        check_operation(task, rev, operation)
        check_window(task, rev)
        check_scope(task, serialize(task), rev)
        if rev.specification.get('stop_when_satisfied', True) and satisfied(s, owner, rev):
            deny(task, 'stop_when_satisfied')


def create_deliverable(s, task_id, data):
    task = s.get(Task, task_id, 'tasks:write')
    version(task, data)
    item = Deliverable(project_id=task.project_id, task_id=task.id, key=data['key'], title=data['title'])
    s.db.add(item)
    s.db.flush()
    append_revision(s, item, data)
    return deliverable_view(s, item)


def append_revision(s, item, data):
    previous = latest(s, item)
    rev = DeliverableRevision(project_id=item.project_id, task_id=item.task_id,
        deliverable_id=item.id, revision=previous.revision + 1 if previous else 1,
        reference=data['reference'], artifact_identity=data['artifact_identity'], actor=s.principal['identity'])
    s.db.add(rev)
    s.db.flush()
    s.event(item.project_id, 'deliverable.revised', item.task_id,
            details={'deliverable_id': item.id, 'revision_id': rev.id, 'version': item.version})


def revise_deliverable(s, ident, data):
    item = s.get(Deliverable, ident, 'tasks:write')
    version(item, data)
    if item.superseded_by:
        raise Problem(409, 'superseded', 'Superseded deliverable cannot be revised')
    item.version += 1
    append_revision(s, item, data)
    return deliverable_view(s, item)


def record_evidence(s, ident, data):
    item = s.get(Deliverable, ident, 'tasks:write')
    version(item, data)
    rev = s.get(DeliverableRevision, data['revision_id'])
    if rev.deliverable_id != ident or rev.artifact_identity != data['artifact_identity']:
        raise Problem(409, 'artifact_mismatch', 'Evidence must match the exact registered revision and identity')
    ev = ValidationEvidence(project_id=item.project_id, task_id=item.task_id, revision_id=rev.id,
        artifact_identity=rev.artifact_identity, outcome=data['outcome'], reference=data['reference'], actor=s.principal['identity'])
    s.db.add(ev)
    item.version += 1
    s.db.flush()
    s.event(item.project_id, 'deliverable.validated', item.task_id,
        details={'deliverable_id': ident, 'revision_id': rev.id, 'evidence_id': ev.id, 'version': item.version})
    return {'evidence': serialize(ev), 'deliverable_version': item.version}


def supersede(s, ident, data):
    item = s.get(Deliverable, ident, 'tasks:write')
    version(item, data)
    target = s.get(Deliverable, data['superseded_by'])
    if item.task_id != target.task_id or item.superseded_by:
        raise Problem(409, 'supersession_conflict', 'Supersession must be within one task and explicit once')
    cursor = target
    while cursor:
        if cursor.id == item.id:
            raise Problem(409, 'supersession_cycle', 'Supersession cannot form a cycle')
        cursor = s.db.get(Deliverable, cursor.superseded_by) if cursor.superseded_by else None
    item.superseded_by = target.id
    item.version += 1
    s.event(item.project_id, 'deliverable.superseded', item.task_id,
        details={'deliverable_id': ident, 'superseded_by': target.id, 'version': item.version})
    return deliverable_view(s, item)


def deliverable_view(s, item):
    revisions = list(s.db.scalars(select(DeliverableRevision).where(DeliverableRevision.deliverable_id == item.id)
                               .order_by(DeliverableRevision.revision)))
    return {**serialize(item), 'revisions': [serialize(r) for r in revisions],
            'evidence': [serialize(e) for e in s.db.scalars(select(ValidationEvidence)
                .where(ValidationEvidence.revision_id.in_([r.id for r in revisions]))
                .order_by(ValidationEvidence.created_at, ValidationEvidence.id))],
            'validated': bool(revisions and good(s, revisions[-1]) and not item.superseded_by)}


def create_checkpoint(s, task_id, data):
    task = s.get(Task, task_id, 'tasks:write')
    version(task, data)
    refs = []
    keys = set()
    for ident in data['revision_ids']:
        rev = s.get(DeliverableRevision, ident)
        ev = good(s, rev)
        if rev.task_id != task.id or not ev:
            raise Problem(409, 'checkpoint_reference', 'Checkpoint requires same-task known-good revisions')
        item = s.get(Deliverable, rev.deliverable_id)
        refs.append((rev, ev))
        if not item.superseded_by and latest(s, item).id == rev.id:
            keys.add(item.key)
    if len(set(data['revision_ids'])) != len(refs):
        raise Problem(422, 'duplicate_reference', 'Checkpoint revisions must be distinct')
    action = data.get('next_authorized_action')
    if action in {'review', 'complete'}:
        state = 'completed' if action == 'complete' else 'review'
        if state not in TRANSITIONS[task.state]:
            raise Problem(409, 'invalid_transition', 'Proposed next action is not currently available')
        from .governance import check_patch
        check_patch(s, task, {'state': state}, True)
    elif action == 'record_evidence':
        # Evidence is permitted even after termination, but needs an actual target.
        target = s.db.scalar(select(DeliverableRevision).where(
            DeliverableRevision.task_id == task.id).limit(1))
        if target is None:
            raise Problem(409, 'evidence_target_missing', 'Recording evidence requires a registered task revision')
        s.get(Deliverable, target.deliverable_id, 'tasks:write')
        s.get(DeliverableRevision, target.id)
    rev = current(s, task.id)
    requirements = set(rev.specification.get('required_deliverables', [])) if rev else set()
    cp = Checkpoint(project_id=task.project_id, task_id=task.id, task_version=task.version,
        contract_ids=[r.id for _, r in ancestry(s, task)], completed_requirements=sorted(keys & requirements),
        blockers=data.get('blockers', []), next_authorized_action=action, actor=s.principal['identity'])
    s.db.add(cp)
    s.db.flush()
    for rev, ev in refs:
        s.db.add(CheckpointRevision(checkpoint_id=cp.id, revision_id=rev.id, evidence_id=ev.id))
    s.db.flush()
    s.event(task.project_id, 'checkpoint.created', task.id, details={'checkpoint_id': cp.id})
    return checkpoint_view(s, cp)


def checkpoint_view(s, cp):
    return {**serialize(cp), 'revisions': [serialize(r) for r in s.db.scalars(select(CheckpointRevision)
            .where(CheckpointRevision.checkpoint_id == cp.id).order_by(CheckpointRevision.revision_id))],
            'restoration': 'read_only_no_resume'}


def fingerprint(values):
    return hashlib.sha256(json.dumps(values, separators=(',', ':'), sort_keys=True).encode()).hexdigest()


def record_exception(s, task_id, data):
    task = s.get(Task, task_id, 'tasks:write')
    version(task, data)
    identity = fingerprint([task.id, data['failure_kind']])
    existing = s.db.scalar(select(Incident).where(Incident.project_id == task.project_id,
        Incident.incident_type == 'task_exception', Incident.resource_id == identity))
    if existing:
        raise Problem(409, 'exception_exists', 'Canonical task/failure exception already exists; inspect its history')
    metadata = {key: data.get(key) for key in ('evidence_reference', 'proposed_action', 'scope_impact')}
    item = Incident(project_id=task.project_id, task_id=task.id, incident_type='task_exception',
        resource_id=identity, failure_kind=data['failure_kind'], description=data['description'], **metadata)
    s.db.add(item)
    s.db.flush()
    s.event(task.project_id, 'exception.recorded', task.id, details={'incident_id': item.id, **metadata})
    return exception_view(s, item)


def exception_view(s, item):
    return {**serialize(item), 'remediation_grant': grant_fields(s, item) if item.task_id else None,
            'remediations': [serialize(r) for r in s.db.scalars(select(Remediation)
                .where(Remediation.incident_id == item.id).order_by(Remediation.created_at, Remediation.id))]}


def grant_fields(s, item):
    task = s.db.get(Task, item.task_id)
    return {'task_id': task.id, 'operation': 'remediation.retry', 'target': task.id,
            'action': 'retry:' + item.failure_kind,
            'artifact': fingerprint([r.id for _, r in ancestry(s, task)])}


def exception_item(s, ident):
    item = s.get(Incident, ident, 'tasks:write')
    if item.incident_type != 'task_exception':
        raise Problem(409, 'incident_type', 'Not a task exception')
    return item


def inspect_exception(s, ident, data):
    item = exception_item(s, ident)
    version(item, data)
    item.version += 1
    item.last_seen_at = now()
    s.event(item.project_id, 'exception.inspected', item.task_id,
            details={'incident_id': item.id, 'version': item.version})
    return exception_view(s, item)


def resolve_exception(s, ident, data):
    item = exception_item(s, ident)
    version(item, data)
    if item.status != 'open':
        raise Problem(409, 'exception_resolved', 'Exception already resolved')
    item.status, item.resolved_at, item.resolution_note = 'resolved', now(), data['note']
    item.version += 1
    s.event(item.project_id, 'exception.resolved', item.task_id,
            details={'incident_id': item.id, 'version': item.version})
    return exception_view(s, item)


def check_exception(s, item):
    task = s.get(Task, item.task_id, 'tasks:write')
    if task.state not in ACTIVE:
        raise Problem(409, 'terminal_task', 'Remediation cannot resume terminal work')
    if item.status != 'open':
        raise Problem(409, 'exception_resolved', 'Remediation requires unresolved exception')
    if item.failure_kind == 'scope':
        deny(task, 'contract_amendment_required')
    check_work(s, task, 'remediation.retry')
    return task


def lineage_ids(s, task):
    result = []
    while task:
        result.append(task.id)
        task = s.db.get(Task, task.parent_id) if task.parent_id else None
    return result


def canonical(item):
    # IDs, prose, grant revision and request keys cannot erase rejection history.
    return fingerprint([item.task_id, item.failure_kind, 'remediation.retry'])


def rejected(s, item):
    if s.db.scalar(select(Approval.id).where(Approval.task_id == item.task_id,
            Approval.operation == 'remediation.retry', Approval.target == item.task_id,
            Approval.action == 'retry:' + item.failure_kind, Approval.status == 'rejected').limit(1)):
        return True
    return any(r.status == 'rejected' or s.db.get(Approval, r.approval_id).status == 'rejected'
        for r in s.db.scalars(select(Remediation).where(Remediation.canonical_identity == canonical(item))))


def request_remediation(s, ident, data):
    item = exception_item(s, ident)
    version(item, data)
    task = check_exception(s, item)
    if rejected(s, item):
        deny(task, 'remediation_rejected')
    approval = s.get(Approval, data['approval_id'], 'approvals:consume')
    if approval.requester != s.principal['identity']:
        raise Problem(403, 'wrong_consumer', 'Only original requester can associate remediation')
    fields = grant_fields(s, item)
    if any(getattr(approval, k) != v for k, v in fields.items()) or approval.project_id != task.project_id:
        raise Problem(409, 'action_mismatch', 'Approval must bind exact failure action and contract revisions')
    if approval.consumed_at or approval.expires_at <= now() or approval.status not in {'pending', 'approved', 'rejected'}:
        raise Problem(409, 'approval_conflict', 'Approval unavailable')
    record = Remediation(project_id=task.project_id, task_id=task.id, incident_id=item.id,
        approval_id=approval.id, canonical_identity=canonical(item),
        contract_ids=[r.id for _, r in ancestry(s, task)], budget_task_ids=lineage_ids(s, task),
        actor=s.principal['identity'], status='rejected' if approval.status == 'rejected' else 'requested')
    s.db.add(record)
    item.version += 1
    s.db.flush()
    s.event(task.project_id, 'remediation.requested', task.id,
            details={'remediation_id': record.id, 'incident_id': item.id, 'approval_id': approval.id})
    return serialize(record)


def authorize_remediation(s, ident, data):
    record = s.get(Remediation, ident, 'tasks:write')
    version(record, data)
    item = exception_item(s, record.incident_id)
    task = check_exception(s, item)
    if rejected(s, item):
        deny(task, 'remediation_rejected')
    if record.status != 'requested':
        raise Problem(409, 'remediation_conflict', 'Remediation already handled')
    governing = ancestry(s, task)
    if record.contract_ids != [r.id for _, r in governing] or record.budget_task_ids != lineage_ids(s, task):
        deny(task, 'contract_changed')
    authorized = list(s.db.scalars(select(Remediation).where(Remediation.status == 'authorized')))
    for owner, rev in governing:
        budget = rev.specification.get('retry_budget')
        if budget is not None and sum(owner.id in r.budget_task_ids for r in authorized) >= budget:
            deny(task, 'retry_budget')
        per_failure = rev.specification.get('max_retries_per_failure')
        if per_failure is not None and sum(owner.id in r.budget_task_ids and
                r.canonical_identity == record.canonical_identity for r in authorized) >= per_failure:
            deny(task, 'max_retries_per_failure')
    from .approvals import consume
    consume(s, record.approval_id, {'expected_version': data['expected_approval_version'], **grant_fields(s, item)}, remediation_id=record.id)
    record.status, record.authorized_at = 'authorized', now()
    record.version += 1
    s.event(task.project_id, 'remediation.authorized', task.id,
            details={'remediation_id': record.id, 'approval_id': record.approval_id, 'version': record.version})
    return serialize(record)
