"""Single-use grants. Cooperating executors enforce this protocol externally."""
from datetime import datetime, timedelta, timezone
from .models import Approval, Task, now
from .services import Problem, serialize


def request_approval(service, data):
    service.project(data['project_id'], 'approvals:request')
    task = service.get(Task, data['task_id'])
    if task.project_id != data['project_id']:
        raise Problem(409, 'cross_project', 'Approval task must belong to project')
    if task.state in {'completed', 'failed', 'cancelled'}:
        raise Problem(409, 'terminal_task', 'Cannot request approval for terminal work')
    expiry = datetime.fromisoformat(data['expires_at'].replace('Z', '+00:00'))
    current = datetime.now(timezone.utc)
    if expiry <= current or expiry > current + timedelta(days=7):
        raise Problem(422, 'expiry', 'Approval expiry must be in the next seven days')
    item = Approval(**data, requester=service.principal['identity'])
    service.db.add(item)
    service.db.flush()
    service.event(item.project_id, 'approval.requested', item.task_id, details={'approval_id': item.id})
    return service.resource_view(item)


def resolve(service, ident, data):
    item = service.get(Approval, ident, 'approvals:resolve')
    service.permit(item.project_id, 'approvals:resolve', operator=True)
    if item.version != data['expected_version'] or item.status != 'pending' or item.expires_at <= now():
        raise Problem(409, 'approval_conflict', 'Approval is stale, resolved, or expired')
    item.status = data['decision']
    item.operator = service.principal['identity']
    item.resolved_at, item.resolution_note = now(), data.get('note')
    item.version += 1
    service.event(item.project_id, 'approval.resolved', item.task_id, 'pending', item.status,
                  {'approval_id': item.id, 'version': item.version})
    return service.resource_view(item)


def consume(service, ident, data):
    item = service.get(Approval, ident, 'approvals:consume')
    if item.requester != service.principal['identity']:
        raise Problem(403, 'wrong_consumer', 'Only the original requesting principal may consume')
    if item.version != data['expected_version'] or item.status != 'approved' or item.consumed_at or item.expires_at <= now():
        raise Problem(409, 'approval_conflict', 'Approval is stale, expired, unapproved, or already consumed')
    if any(getattr(item, k) != data[k] for k in ('task_id', 'operation', 'target', 'action', 'artifact')):
        raise Problem(409, 'action_mismatch', 'Action, target, task, or artifact does not match')
    task = service.get(Task, item.task_id)
    if task.state in {'completed', 'failed', 'cancelled'}:
        raise Problem(409, 'terminal_task', 'Cannot consume authorization for terminal work')
    item.consumed_at, item.consumed_by = now(), service.principal['identity']
    item.version += 1
    service.event(item.project_id, 'approval.consumed', item.task_id,
                  details={'approval_id': item.id, 'version': item.version})
    return service.resource_view(item)


def expire(service):
    """Administrative housekeeping; expiry checks never depend on running this."""
    from sqlalchemy import select
    for item in service.db.scalars(select(Approval).where(
            Approval.status.in_(['pending', 'approved']), Approval.consumed_at.is_(None), Approval.expires_at <= now())):
        previous = item.status
        item.status = 'expired'
        item.version += 1
        service.event(item.project_id, 'approval.expired', item.task_id, previous, 'expired', {'approval_id': item.id})


def view(item):
    result = serialize(item)
    result['effective_status'] = ('expired' if item.status in {'pending', 'approved'} and not item.consumed_at
                                  and item.expires_at <= now() else item.status)
    return result
