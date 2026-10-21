"""Contract policy for the established Service transaction boundary; no execution."""
from datetime import datetime
from sqlalchemy import select, func
from .models import Task, TaskContract, Delegation, now
from .schemas import ContractSpec
from .state_machine import ACTIVE


class Denied(Exception):
    def __init__(self, project, task, code):
        self.project, self.task, self.code = project, task, code


def current(s, task_id):
    return s.db.scalar(select(TaskContract).where(TaskContract.task_id == task_id)
                       .order_by(TaskContract.revision.desc()).limit(1))


def ancestry(s, task):
    result = []
    while task:
        revision = current(s, task.id)
        if revision:
            result.append((task, revision))
        task = s.db.get(Task, task.parent_id) if task.parent_id else None
    return result


def deny(task, code):
    raise Denied(task.project_id, task.id, code)


def labels(data):
    return set(data.get('resource_labels') or []) | {
        data[k] for k in ('repository_path', 'worktree_path') if data.get(k) is not None}


def check_scope(task, data, revision):
    allowed = revision.specification['resource_labels']
    if allowed is not None and not labels(data).issubset(set(allowed)):
        deny(task, 'resource_scope')


def check_window(task, revision):
    seconds = revision.specification['runtime_seconds']
    if seconds is not None:
        elapsed = (datetime.fromisoformat(now().replace('Z', '+00:00')) -
                   datetime.fromisoformat(revision.authorization_started_at.replace('Z', '+00:00'))).total_seconds()
        if elapsed >= seconds:
            deny(task, 'authorization_expired')


def check_operation(task, revision, operation, state=None):
    # Unknown labels are advisory, never arbitrary runtime/tool enforcement.
    prohibited = revision.specification.get('prohibited_operations', [])
    if operation in prohibited or (state and f'{operation}.{state}' in prohibited):
        deny(task, 'prohibited_operation')


def consumed(s, task_id):
    return s.db.scalar(select(func.count()).select_from(Delegation).where(Delegation.parent_id == task_id))


def check_create(s, data):
    parent = s.db.get(Task, data.get('parent_id')) if data.get('parent_id') else None
    governing = ancestry(s, parent)
    if governing and not (data.get('spawn_reason') or '').strip():
        deny(parent, 'spawn_reason_required')
    distance = 1
    cursor = parent
    while cursor:
        revision = current(s, cursor.id)
        if revision:
            check_operation(parent, revision, 'task.create')
            check_window(parent, revision)
            check_scope(parent, data, revision)
            spec = revision.specification
            if spec['max_descendant_depth'] is not None and distance > spec['max_descendant_depth']:
                deny(parent, 'descendant_depth')
            if distance == 1 and spec['max_children'] is not None and consumed(s, cursor.id) >= spec['max_children']:
                deny(parent, 'child_budget')
        distance += 1
        cursor = s.db.get(Task, cursor.parent_id) if cursor.parent_id else None
    return governing


def record_delegation(s, item, reason=None):
    if not item.parent_id:
        return
    parent = s.db.get(Task, item.parent_id)
    revision = current(s, parent.id)
    s.db.add(Delegation(task_id=item.id, parent_id=parent.id,
        parent_contract_id=revision.id if revision else None,
        governing_contract_ids=[r.id for _, r in ancestry(s, parent)],
        authorized_by=s.principal['identity'], spawn_reason=reason))


def check_patch(s, item, data, transition):
    governing = ancestry(s, item)
    if not transition and 'parent_id' in data and data['parent_id'] != item.parent_id:
        # Conservative: governed lineage cannot be moved, even by operators. This
        # checks the entire subtree, including contracts below an ungoverned root.
        nodes = s.task_tree(ident=item.id)['nodes']
        destination = s.db.get(Task, data['parent_id']) if data['parent_id'] else None
        if governing or ancestry(s, destination) or any(current(s, n['id']) for n in nodes):
            deny(item, 'governed_reparent')
    if transition and data['state'] in {'failed', 'cancelled'}:
        return
    for _, revision in governing:
        check_operation(item, revision, 'task.transition' if transition else 'task.patch',
                        data['state'] if transition else None)
        check_window(item, revision)
    if governing and not transition:
        protected = {'objective', 'repository_path', 'worktree_path', 'base_commit', 'resource_labels'}
        if any(k in data and data[k] != getattr(item, k) for k in protected):
            deny(item, 'contract_amendment_required')


def write_contract(s, ident, data):
    from .services import Problem, serialize
    item = s.get(Task, ident, 'tasks:write')
    s.permit(item.project_id, 'tasks:write', operator=True)
    previous = current(s, ident)
    revision = previous.revision if previous else 0
    if item.version != data['expected_version'] or revision != data['expected_contract_version']:
        raise Problem(409, 'stale_version', 'Task or contract version has changed')
    if item.state not in ACTIVE:
        raise Problem(409, 'terminal_task', 'Terminal tasks are immutable')
    if not data['reason'].strip():
        raise Problem(422, 'validation', 'Amendment reason must not be blank')
    spec = ContractSpec.model_validate(data['contract']).model_dump()
    proposed = {**serialize(item), **{k: spec[k] for k in
                ('objective', 'repository_path', 'worktree_path', 'base_commit')}}
    if spec['resource_labels'] is not None:
        proposed['resource_labels'] = spec['resource_labels']
    for _, ancestor in ancestry(s, s.db.get(Task, item.parent_id) if item.parent_id else None):
        check_window(item, ancestor)
        check_scope(item, proposed, ancestor)
    record = TaskContract(task_id=ident, revision=revision + 1, specification=spec,
        expected_task_version=data['expected_version'], expected_contract_version=revision,
        actor=s.principal['identity'], reason=data['reason'],
        authorization_started_at=previous.authorization_started_at if previous else now())
    check_scope(item, proposed, record)
    if spec['max_children'] is not None and consumed(s, ident) > spec['max_children']:
        deny(item, 'child_budget')
    for node in s.task_tree(ident=ident)['nodes'][1:]:
        check_scope(item, node, record)
        if spec['max_descendant_depth'] is not None and node['depth'] > spec['max_descendant_depth']:
            deny(item, 'descendant_depth')
    # Validation is complete before changing the task or appending its revision.
    for k in ('objective', 'repository_path', 'worktree_path', 'base_commit', 'resource_labels'):
        setattr(item, k, proposed[k])
    item.version += 1
    item.updated_at = now()
    s.db.add(record)
    s.db.flush()
    s.event(item.project_id, 'contract.revised', ident,
            details={'contract_id': record.id, 'revision': record.revision, 'version': item.version})
    return {'contract': serialize(record), 'task_version': item.version}


def history(s, ident):
    from .services import serialize
    s.get(Task, ident)
    records = list(s.db.scalars(select(TaskContract).where(TaskContract.task_id == ident)
                               .order_by(TaskContract.revision)))
    return {'current': serialize(records[-1]) if records else None,
            'history': [serialize(r) for r in records]}
