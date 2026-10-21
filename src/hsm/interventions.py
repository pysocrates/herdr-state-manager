"""Reversible governance and read-only scope; never process control."""
from datetime import datetime
from sqlalchemy import select
from .models import Task, Deliverable, Incident, Remediation, now
from .services import Problem, serialize
from .governance import ancestry, current, consumed, deny
from .state_machine import ACTIVE


def paused_ancestors(s, task):
    result = []
    while task:
        if task.governance_paused:
            result.append(task.id)
        task = s.db.get(Task, task.parent_id) if task.parent_id else None
    return result


def check_unpaused(s, task):
    if paused_ancestors(s, task):
        deny(task, 'governance_paused')


def intervene(s, ident, data, paused):
    item = s.get(Task, ident, 'tasks:write')
    s.permit(item.project_id, 'tasks:write', operator=True)
    if item.version != data['expected_version']:
        raise Problem(409, 'stale_version', 'Task version has changed')
    if item.state not in ACTIVE:
        raise Problem(409, 'terminal_task', 'Terminal tasks are immutable')
    if item.governance_paused == paused:
        raise Problem(409, 'governance_conflict', 'Task already has requested local governance state')
    item.governance_paused = paused
    item.governance_changed_at = now()
    item.governance_changed_by = s.principal['identity']
    item.governance_reason = data['reason']
    item.updated_at = item.governance_changed_at
    item.version += 1
    s.event(item.project_id, 'task.paused' if paused else 'task.resumed', item.id,
            details={'reason': data['reason'], 'version': item.version,
                     'paused': paused, 'runtime_control_supported': False})
    s.db.flush()
    return s.task_view(item)


def unresolved(s, task):
    return list(s.db.scalars(select(Incident).where(Incident.task_id == task.id,
        Incident.incident_type == 'task_exception', Incident.status == 'open').order_by(Incident.id)))


def summary(s, task):
    from .durable import completed
    revision = current(s, task.id)
    spec = revision.specification if revision else {}
    limit = spec.get('max_children')
    return {'paused': task.governance_paused, 'effective_paused': bool(paused_ancestors(s, task)),
            'remaining_children': max(0, limit - consumed(s, task.id)) if limit is not None else None,
            'ready_deliverables': len(completed(s, task.id)),
            'required_deliverables': len(set(spec.get('required_deliverables', []))),
            'unresolved_exceptions': len(unresolved(s, task))}


def scope(s, ident, timestamp=None):
    from .durable import deliverable_view, exception_view
    task = s.get(Task, ident)
    governing = ancestry(s, task)
    timestamp = timestamp or now()
    clock = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    authorized = list(s.db.scalars(select(Remediation).where(Remediation.project_id == task.project_id,
                                                            Remediation.status == 'authorized')))
    constraints = []
    for owner, revision in governing:
        spec = revision.specification
        distance, cursor = 0, task
        while cursor.id != owner.id:
            distance += 1
            cursor = s.db.get(Task, cursor.parent_id)
        seconds = spec.get('runtime_seconds')
        elapsed = (clock - datetime.fromisoformat(revision.authorization_started_at.replace('Z', '+00:00'))).total_seconds()
        retries = [r for r in authorized if owner.id in r.budget_task_ids]
        per_failure = spec.get('max_retries_per_failure')
        identities = sorted({r.canonical_identity for r in retries})
        constraints.append({'task_id': owner.id, 'contract_id': revision.id,
            'contract_revision': revision.revision, 'distance': distance,
            'limits': {key: spec.get(key) for key in ('max_children', 'max_descendant_depth', 'runtime_seconds', 'retry_budget', 'max_retries_per_failure')},
            'used': {'delegation_authorizations': consumed(s, owner.id), 'authorized_retries': len(retries),
                     'elapsed_authorization_seconds': max(0, elapsed)},
            'remaining_children': max(0, spec['max_children'] - consumed(s, owner.id)) if spec.get('max_children') is not None else None,
            'remaining_descendant_depth': max(0, spec['max_descendant_depth'] - distance) if spec.get('max_descendant_depth') is not None else None,
            'remaining_runtime_seconds': max(0, seconds - elapsed) if seconds is not None else None,
            'remaining_retries': max(0, spec['retry_budget'] - len(retries)) if spec.get('retry_budget') is not None else None,
            'max_retries_per_failure': per_failure,
            'remaining_retries_by_failure': {identity: max(0, per_failure - sum(r.canonical_identity == identity for r in retries)) for identity in identities} if per_failure is not None else None})
    def minimum(key):
        values = [c[key] for c in constraints if c[key] is not None]
        return min(values) if values else None
    view = s.task_view(task)
    paused = paused_ancestors(s, task)
    return {'task': view, 'contracts': [serialize(r) for _, r in governing],
        'remaining_budget': {'remaining_children': view['governance']['remaining_children'],
            'remaining_descendant_depth': minimum('remaining_descendant_depth'),
            'remaining_runtime_seconds': minimum('remaining_runtime_seconds'),
            'remaining_retries': minimum('remaining_retries'), 'constraints': constraints},
        'deliverables': [deliverable_view(s, d) for d in s.db.scalars(select(Deliverable).where(Deliverable.task_id == ident).order_by(Deliverable.id))],
        'unresolved_exceptions': [exception_view(s, e) for e in unresolved(s, task)],
        'runtime_control': {'supported': False, 'acknowledged': False},
        'governance_decision': {'paused_ancestor_ids': paused, 'new_work_blocked_by_pause': bool(paused),
            'terminal': task.state not in ACTIVE, 'authorization': 'recheck_at_mutation'},
        'generated_at': timestamp}
