"""Shared domain boundary. Callers supply a verified principal and transaction."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from .models import Project, Task, AgentSession, Pane, Event, Idempotency, Approval, Incident, AdapterHealth, now
from .state_machine import TRANSITIONS, ACTIVE
from .hierarchy import task_forest, subtree


class Problem(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def serialize(obj):
    return {column.name: getattr(obj, column.name) for column in obj.__table__.columns}


class Service:
    def __init__(self, session, principal):
        self.db, self.principal = session, principal

    def permit(self, project, action='read', operator=False):
        p = self.principal
        if (operator and p['role'] != 'operator' or
                project not in p['projects'] and '*' not in p['projects'] or
                action not in p['actions'] and '*' not in p['actions']):
            raise Problem(403, 'forbidden', 'Credential does not grant this project/action')

    def project(self, project, action='read', operator=False):
        self.permit(project, action, operator)
        item = self.db.get(Project, project)
        if item is None:
            raise Problem(404, 'not_found', 'Project not found')
        if action != 'read' and not item.enabled:
            raise Problem(409, 'disabled_project', 'Project is disabled')
        return item

    def get(self, model, ident, action='read'):
        item = self.db.get(model, ident)
        if item is None:
            raise Problem(404, 'not_found', 'Resource not found')
        self.project(item.project_id, action)
        return item

    def event(self, project, kind, task=None, previous=None, new=None, details=None):
        details = dict(details or {})
        if task:
            from .governance import ancestry
            # Snapshot now, including after a denial's savepoint rollback. Never
            # reinterpret old events using the task's later contract revisions.
            details['governing_contract_ids'] = [
                revision.id for _, revision in ancestry(self, self.db.get(Task, task))]
        self.db.add(Event(project_id=project, task_id=task, actor=self.principal['identity'],
                          event_type=kind, previous_state=previous, new_state=new, details=details))

    def projects(self):
        query = select(Project).order_by(Project.id)
        if '*' not in self.principal['projects']:
            query = query.where(Project.id.in_(self.principal['projects']))
        if 'read' not in self.principal['actions'] and '*' not in self.principal['actions']:
            raise Problem(403, 'forbidden', 'Read permission required')
        return list(self.db.scalars(query))

    def create_project(self, data):
        self.permit(data['id'], 'projects:write', operator=True)
        item = Project(**data)
        self.db.add(item)
        self.db.flush()
        self.event(item.id, 'project.created')
        return serialize(item)

    def references(self, project, data, task_id=None):
        for key, model in [('parent_id', Task), ('owner_session_id', AgentSession),
                           ('task_id', Task), ('agent_session_id', AgentSession)]:
            if data.get(key):
                ref = self.get(model, data[key])
                if ref.project_id != project:
                    raise Problem(409, 'cross_project', 'References must belong to the same project')
                if key == 'parent_id':
                    visited = {task_id} if task_id else set()
                    while ref:
                        if ref.id in visited:
                            raise Problem(409, 'parent_cycle', 'Task parent cycle')
                        visited.add(ref.id)
                        ref = self.db.get(Task, ref.parent_id) if ref.parent_id else None

    def create_task(self, data):
        self.project(data['project_id'], 'tasks:write')
        self.references(data['project_id'], data)
        from .governance import check_create, record_delegation
        check_create(self, data)
        item = Task(**{k: v for k, v in data.items() if k != 'spawn_reason'})
        self.db.add(item)
        self.db.flush()
        record_delegation(self, item, data.get('spawn_reason'))
        self.event(item.project_id, 'task.created', item.id, new=item.state,
                   details={'parent_id': item.parent_id, 'owner_session_id': item.owner_session_id})
        return self.task_view(item)

    def patch_task(self, ident, data, transition=False):
        item = self.get(Task, ident, 'tasks:write')
        if item.version != data['expected_version']:
            raise Problem(409, 'stale_version', 'Task version has changed')
        if item.state not in ACTIVE:
            raise Problem(409, 'terminal_task', 'Terminal tasks are immutable')
        from .governance import check_patch, record_delegation
        if not transition:
            self.references(item.project_id, data, ident)
        check_patch(self, item, data, transition)
        previous = item.state
        assignment_changes = {key: {'previous_id': getattr(item, key), 'new_id': data[key]}
                              for key in ('parent_id', 'owner_session_id')
                              if key in data and data[key] != getattr(item, key)}
        if transition:
            if data['state'] not in TRANSITIONS[item.state]:
                raise Problem(409, 'invalid_transition', 'Transition is not allowed')
            item.state = data['state']
            if item.state not in ACTIVE:
                item.completed_at = now()
        else:
            self.references(item.project_id, data, ident)
            for key, value in data.items():
                if key != 'expected_version':
                    if key in {'title', 'objective'} and value is None:
                        raise Problem(422, 'validation', f'{key} cannot be null')
                    setattr(item, key, value)
        item.version += 1
        if 'parent_id' in assignment_changes:
            record_delegation(self, item)
        item.updated_at = now()
        self.event(item.project_id, 'task.transitioned' if transition else 'task.updated',
                   item.id, previous if transition else None, item.state if transition else None,
                   {'version': item.version, **assignment_changes})
        self.db.flush()
        return self.task_view(item)

    def register(self, model, data):
        self.project(data['project_id'], 'runtime:write')
        self.references(data['project_id'], data)
        item = model(**data)
        self.db.add(item)
        self.db.flush()
        self.event(item.project_id, f'{model.__tablename__}.registered',
                   data.get('task_id'), details={'resource_id': item.id})
        return serialize(item)

    def runtime_update(self, model, ident, data, heartbeat=False):
        item = self.get(model, ident, 'runtime:write')
        self.references(item.project_id, data)
        if model is AgentSession and any(k in data for k in ('task_id', 'agent_session_id')):
            raise Problem(422, 'validation', 'Session cannot have pane assignments')
        for key, value in data.items():
            if key == 'runtime_status' and value is None:
                raise Problem(422, 'validation', 'runtime_status cannot be null')
            setattr(item, key, value)
        if heartbeat:
            item.heartbeat_at = now()
        else:
            item.last_observed_at = now()
        if data.get('runtime_status') == 'terminated':
            if model is Pane:
                item.terminated_at = now()
            else:
                item.ended_at = now()
        self.event(item.project_id, f'{model.__tablename__}.heartbeat' if heartbeat else f'{model.__tablename__}.updated',
                   getattr(item, 'task_id', None), details={'resource_id': ident})
        self.db.flush()
        return serialize(item)

    def guarded(self, operation):
        """Only policy denials commit sanitized evidence, after savepoint rollback."""
        from .governance import Denied
        try:
            with self.db.begin_nested():
                return operation()
        except Denied as error:
            self.event(error.project, 'governance.denied', error.task,
                       details={'code': error.code})
            return {'error': {'code': error.code, 'message': 'Contract authorization denied', 'details': []}}, 409

    def replay(self, route, key, data, operation):
        if not key or len(key) > 128 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise Problem(422, 'idempotency_key', 'A printable Idempotency-Key (1..128) is required')
        scope = json.dumps([self.principal['identity'], route, key])
        digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        old = self.db.get(Idempotency, scope)
        if old and old.expires_at > now():
            if old.digest != digest:
                raise Problem(409, 'idempotency_mismatch', 'Key already used with another payload')
            return old.response, old.status
        if old:
            self.db.delete(old)
            self.db.flush()
        body, status = self.guarded(operation)
        self.db.add(Idempotency(scope=scope, digest=digest, response=body, status=status,
                               expires_at=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat().replace('+00:00', 'Z')))
        return body, status

    def listing(self, model, filters, limit, offset):
        projects = [p.id for p in self.projects()]
        if filters.get('project'):
            self.project(filters['project'])
            projects = [filters['project']]
        query = select(model).where(model.project_id.in_(projects))
        if model is Task and 'parent_id' in filters:
            if filters['parent_id'] == 'none':
                query = query.where(Task.parent_id.is_(None))
            else:
                self.get(Task, filters['parent_id'])
                query = query.where(Task.parent_id == filters['parent_id'])
        for name in ('state', 'task_id', 'status'):
            if filters.get(name):
                query = query.where(getattr(model, name) == filters[name])
        if filters.get('profile'):
            if model is Task:
                query = query.join(AgentSession, Task.owner_session_id == AgentSession.id).where(AgentSession.profile == filters['profile'])
            elif model is AgentSession:
                query = query.where(AgentSession.profile == filters['profile'])
        if filters.get('created_after'):
            query = query.where(model.created_at >= filters['created_after'])
        if filters.get('created_before'):
            query = query.where(model.created_at <= filters['created_before'])
        ordering = Event.cursor.desc() if model is Event else model.id
        items = list(self.db.scalars(query.order_by(ordering).offset(offset).limit(limit + 1)))
        task_views = {n['id']: n for n in self.project_forest(projects)} if model is Task else {}
        return {'data': [task_views[i.id] if model is Task else self.resource_view(i) for i in items[:limit]],
                'pagination': {'offset': offset, 'limit': limit, 'has_more': len(items) > limit}}

    def project_forest(self, project_ids):
        """Internal read, called only after project authorization."""
        tasks = [serialize(t) for t in self.db.scalars(select(Task).where(Task.project_id.in_(project_ids)))]
        sessions = {s.id: s for s in self.db.scalars(select(AgentSession).where(AgentSession.project_id.in_(project_ids)))}
        panes = {}
        for pane in self.db.scalars(select(Pane).where(Pane.project_id.in_(project_ids), Pane.terminated_at.is_(None)).order_by(Pane.id)):
            panes.setdefault(pane.task_id, []).append(pane.id)
        from .models import Delegation
        delegations = {}
        for record in self.db.scalars(select(Delegation).join(Task, Delegation.task_id == Task.id)
                .where(Task.project_id.in_(project_ids)).order_by(Delegation.created_at, Delegation.id)):
            delegations.setdefault(record.task_id, []).append(serialize(record))
        for task in tasks:
            owner = sessions.get(task['owner_session_id'])
            task['owner_profile'] = owner.profile if owner else None
            task['pane_ids'] = panes.get(task['id'], [])
            task['delegation_history'] = delegations.get(task['id'], [])
        return task_forest(tasks)

    def task_view(self, item):
        return next(n for n in self.project_forest([item.project_id]) if n['id'] == item.id)

    def task_tree(self, project=None, ident=None):
        if ident:
            task = self.get(Task, ident)
            if project is not None and project != task.project_id:
                self.project(project)
                raise Problem(409, 'project_mismatch', 'Task does not belong to requested project')
            nodes = subtree(self.project_forest([task.project_id]), ident)
        else:
            ids = [self.project(project).id] if project else [p.id for p in self.projects()]
            nodes = self.project_forest(ids)
        return {'nodes': nodes, 'root_ids': [n['id'] for n in nodes if n['depth'] == 0],
                'selection': 'subtree' if ident else 'all', 'truncation': {'nodes': False}}

    def resource_view(self, item):
        if isinstance(item, Approval):
            from .approvals import view
            result = view(item)
            task = self.db.get(Task, item.task_id)
            result['task_state'], result['task_key'] = task.state, task.task_key
            return result
        return serialize(item)

    def snapshot(self, project=None, recent=30):
        projects = self.projects()
        if project:
            projects = [self.project(project)]
        ids = [p.id for p in projects]
        def rows(model, *conditions):
            return [self.resource_view(x) for x in self.db.scalars(select(model).where(
                model.project_id.in_(ids), *conditions).order_by(model.project_id, model.id))]
        forest = self.project_forest(ids)
        by_id = {n['id']: n for n in forest}
        tasks = [by_id[t['id']] for t in rows(Task, Task.state.in_(ACTIVE))]
        relevant = {t['id'] for t in tasks}
        for task in tasks:
            parent = task['parent_id']
            while parent and parent not in relevant:
                relevant.add(parent)
                parent = by_id[parent]['parent_id']
        hierarchy = [node for node in forest if node['id'] in relevant]
        task_ids = [t['id'] for t in tasks]
        owner_ids = [t['owner_session_id'] for t in tasks if t['owner_session_id']]
        events = list(self.db.scalars(select(Event).where(Event.project_id.in_(ids))
                                     .order_by(Event.cursor.desc()).limit(recent + 1)))
        from .responses import Snapshot
        result = {'schema_version': 1, 'project': project, 'generated_at': now(),
                'revision': self.db.scalar(select(func.max(Event.cursor)).where(Event.project_id.in_(ids))) or 0,
                'projects': [serialize(p) for p in projects], 'active_tasks': tasks,
                'task_hierarchy': {'selection': 'active_with_ancestors', 'nodes': hierarchy, 'root_ids': [n['id'] for n in hierarchy if n['depth'] == 0], 'truncation': {'nodes': False}},
                'agent_sessions': rows(AgentSession, AgentSession.ended_at.is_(None)),
                'active_panes': rows(Pane, Pane.terminated_at.is_(None)),
                'related_ended_sessions': rows(AgentSession, AgentSession.ended_at.is_not(None), AgentSession.id.in_(owner_ids)),
                'related_terminated_panes': rows(Pane, Pane.terminated_at.is_not(None), Pane.task_id.in_(task_ids)),
                'pending_approvals': rows(Approval, Approval.status == 'pending', Approval.expires_at > now()),
                'outstanding_approvals': [self.resource_view(a) for a in self.db.scalars(select(Approval).where(Approval.project_id.in_(ids), Approval.status.in_(['pending', 'approved']), Approval.consumed_at.is_(None)).order_by(Approval.project_id, Approval.id))], 'blocked_tasks': [t for t in tasks if t['state'] == 'blocked'],
                'unresolved_incidents': rows(Incident, Incident.status == 'open'),
                'adapter_health': rows(AdapterHealth), 'recent_events': [serialize(e) for e in events[:recent]],
                'warnings': self.snapshot_warnings(ids) + [f"{n['project_id']}/{n['task_key']}: terminal parent has {n['descendant_summary']['active']} active descendants" for n in hierarchy if n['state'] not in ACTIVE and n['descendant_summary']['active']],
                'truncation': {'recent_events': len(events) > recent}}
        return Snapshot.model_validate(result).model_dump()

    def snapshot_warnings(self, projects):
        health = list(self.db.scalars(select(AdapterHealth).where(AdapterHealth.project_id.in_(projects))))
        warnings = [f'{p}: {provider} has no runtime observation' for p in projects for provider in ('herdr', 'hermes')
                    if not any(h.project_id == p and h.provider == provider for h in health)]
        warnings.extend(f'{h.project_id}: {h.provider} {h.status} at {h.observed_at}' for h in health if h.status != 'available')
        return sorted(warnings)
