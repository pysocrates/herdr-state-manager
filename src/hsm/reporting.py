"""Versioned, read-only governance projections. Caller owns one read transaction.

No adapter calls, intent inference, authorization consumption or implicit resume.
History completeness is reported rather than reconstructed from mutable lineage.
"""
from collections import Counter
from datetime import datetime
from sqlalchemy import select, func
from .models import (Task, Event, Checkpoint, AgentSession, Remediation, Incident,
                     Delegation, DeliverableRevision, TaskContract, now)
from .services import serialize, Problem
from .state_machine import ACTIVE, TRANSITIONS
from .interventions import scope
from .durable import checkpoint_view, completed, good

THRESHOLDS = {'depth_remaining_at_most': 1, 'repeated_count_at_least': 3,
              'stalled_exception_seconds_at_least': 3600}
AUTHORIZED_EVENTS = {
    'task.created', 'task.updated', 'task.transitioned', 'task.paused', 'task.resumed',
    'contract.revised', 'deliverable.revised', 'deliverable.validated',
    'deliverable.superseded', 'remediation.authorized', 'approval.consumed',
}


def clock(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def cursor(s, projects):
    return s.db.scalar(select(func.max(Event.cursor)).where(Event.project_id.in_(projects))) or 0


def unavailable(reason):
    return {'status': 'unavailable', 'value': None, 'reason': reason}


def next_action(s, task, view, checkpoint):
    """Conservative eligibility, not a payload/grant. Recheck at mutation always."""
    action = checkpoint.next_authorized_action if checkpoint else None
    if not action:
        return None, 'no_recorded_action'
    if task.state not in ACTIVE:
        return None, 'terminal_task'
    if view['governance_decision']['new_work_blocked_by_pause']:
        return None, 'governance_paused'
    if checkpoint.task_version != task.version:
        return None, 'task_version_changed'
    if checkpoint.contract_ids != [r['id'] for r in view['contracts']]:
        return None, 'contract_changed'
    try:
        s.project(task.project_id, 'tasks:write')
    except Problem as error:
        return None, error.code
    for reference in checkpoint_view(s, checkpoint)['revisions']:
        revision = s.db.get(DeliverableRevision, reference['revision_id'])
        evidence = good(s, revision)
        if not evidence or evidence.id != reference['evidence_id']:
            return None, 'checkpoint_evidence_changed'
    if action == 'record_evidence':
        if not any(d['revisions'] for d in view['deliverables']):
            return None, 'evidence_target_missing'
    elif action in {'review', 'complete'}:
        state = 'review' if action == 'review' else 'completed'
        if state not in TRANSITIONS[task.state]:
            return None, 'invalid_transition'
        for contract in view['contracts']:
            prohibited = contract['specification'].get('prohibited_operations', [])
            if 'task.transition' in prohibited or 'task.transition.' + state in prohibited:
                return None, 'prohibited_operation'
        remaining = view['remaining_budget']['remaining_runtime_seconds']
        if remaining is not None and remaining <= 0:
            return None, 'authorization_expired'
        if action == 'complete' and view['readiness']['missing_required_keys']:
            return None, 'required_deliverables_missing'
    else:
        return None, 'unsupported_recorded_action'
    return action, 'eligible_read_only_recheck_at_mutation'


def recovery(s, ident, as_of=None, event_cursor=None):
    task = s.get(Task, ident)
    as_of = as_of or now()
    event_cursor = cursor(s, [task.project_id]) if event_cursor is None else event_cursor
    view = scope(s, ident, as_of)
    local = next((c for c in view['contracts'] if c['task_id'] == ident), None)
    required = set(local['specification'].get('required_deliverables', [])) if local else set()
    ready = completed(s, ident)
    checkpoints = list(s.db.scalars(select(Checkpoint).where(Checkpoint.task_id == ident)
                                    .order_by(Checkpoint.created_at, Checkpoint.id)))
    owner = s.db.get(AgentSession, task.owner_session_id) if task.owner_session_id else None
    last = s.db.scalar(select(Event).where(Event.task_id == ident,
        Event.event_type.in_(AUTHORIZED_EVENTS)).order_by(Event.cursor.desc()).limit(1))
    view.update(schema_version=1, as_of=as_of, event_cursor=event_cursor,
        objective=task.objective, local_contract=local,
        checkpoints=[checkpoint_view(s, c) for c in checkpoints],
        latest_checkpoint=checkpoint_view(s, checkpoints[-1]) if checkpoints else None,
        ownership={'owner_session_id': task.owner_session_id,
                   'session': serialize(owner) if owner else None, 'source': 'persisted_agent_sessions'},
        readiness={'required_keys': sorted(required), 'validated_keys': sorted(ready),
                   'missing_required_keys': sorted(required - ready),
                   'requirements_satisfied': required <= ready,
                   'has_requirements': bool(required), 'task_completed': task.state == 'completed'},
        last_recorded_authorized_action=serialize(last) if last else None,
        restoration='read_only_no_resume',
        runtime={'source': 'persisted_agent_sessions',
                 'status': owner.runtime_status if owner else None,
                 'observed_at': owner.last_observed_at if owner else None,
                 'heartbeat_at': owner.heartbeat_at if owner else None,
                 'context_utilization': unavailable('no_authoritative_context_telemetry'),
                 'execution_runtime_seconds': unavailable('authorization_window_is_not_execution_runtime')})
    action, reason = next_action(s, task, view, checkpoints[-1] if checkpoints else None)
    view['next_authorized_action'] = action
    view['next_action_reason'] = reason
    return view


def historical_creations(events):
    """Replay recorded parent changes at each creation cursor; never today's tree."""
    parents, result = {}, []
    for event in events:
        if event.event_type == 'task.created':
            if 'parent_id' not in event.details:
                continue
            parent = event.details['parent_id']
            parents[event.task_id] = parent
            if parent is not None:
                root, seen = parent, {event.task_id}
                while root in parents and parents[root] is not None and root not in seen:
                    seen.add(root)
                    root = parents[root]
                known = root in parents and root not in seen and parents[root] is None
                result.append((event.task_id, root if known else None, event.cursor))
        elif event.event_type == 'task.updated' and 'parent_id' in event.details:
            change = event.details['parent_id']
            if isinstance(change, dict) and 'new_id' in change:
                parents[event.task_id] = change['new_id']
    return result


def metrics(s, project=None, ident=None, as_of=None, event_cursor=None):
    task = s.get(Task, ident) if ident else None
    ids = [task.project_id] if task else ([s.project(project).id] if project else [p.id for p in s.projects()])
    as_of = as_of or now()
    event_cursor = cursor(s, ids) if event_cursor is None else event_cursor
    events = list(s.db.scalars(select(Event).where(Event.project_id.in_(ids)).order_by(Event.cursor)))
    forest = s.project_forest(ids)
    selected = [n for n in forest if not ident or n['id'] == ident]
    selected_ids = {n['id'] for n in selected}
    relevant = [e for e in events if e.task_id in selected_ids]
    denials = [e for e in relevant if e.event_type == 'governance.denied']
    creations = [row for row in historical_creations(events) if row[0] in selected_ids]
    historical = Counter(root for _, root, _ in creations if root is not None)
    current_roots = Counter(n['root_id'] for n in selected if n['parent_id'])
    revisions = list(s.db.scalars(select(TaskContract).where(TaskContract.task_id.in_(selected_ids))))
    amendments = [revision for revision in revisions if revision.revision > 1]
    amendment_denials = [e for e in denials if e.details.get('request_type') == 'contract.amend']
    delegation_denials = [e for e in denials if e.details.get('request_type') == 'delegation.create']
    remediations = list(s.db.scalars(select(Remediation).where(Remediation.task_id.in_(selected_ids)).order_by(Remediation.id)))
    exceptions = list(s.db.scalars(select(Incident).where(Incident.task_id.in_(selected_ids),
        Incident.incident_type == 'task_exception').order_by(Incident.id)))
    delegation_count = s.db.scalar(select(func.count()).select_from(Delegation).where(Delegation.task_id.in_(selected_ids)))
    recoveries = [recovery(s, n['id'], as_of, event_cursor) for n in sorted(selected, key=lambda n: (n['project_id'], n['id']))]
    alerts, orphans, stalled = [], [], []
    def alert(code, task_id, evidence):
        alerts.append({'code': code, 'task_id': task_id, 'evidence': evidence})
    for view in recoveries:
        row = view['task']
        tid = row['id']
        depth = view['remaining_budget']['remaining_descendant_depth']
        if depth is not None and depth <= THRESHOLDS['depth_remaining_at_most']:
            alert('depth_limit_approached', tid, {'remaining_descendant_depth': depth})
        denied = sum(e.task_id == tid for e in delegation_denials)
        if denied >= THRESHOLDS['repeated_count_at_least']:
            alert('repeated_denied_expansion', tid, {'observed_denials': denied})
        count = sum(e.task_id == tid for e in exceptions)
        if count >= THRESHOLDS['repeated_count_at_least']:
            alert('multiple_canonical_exceptions', tid, {'canonical_count': count, 'recurrence': None})
        retries = sum(r.task_id == tid and r.status == 'authorized' for r in remediations)
        if retries >= THRESHOLDS['repeated_count_at_least']:
            alert('repeated_authorized_retries', tid, {'authorized_retries': retries})
        missing = view['readiness']['missing_required_keys']
        if missing and (row['state'] not in ACTIVE or any(e.task_id == tid and
                e.details.get('code') == 'required_deliverables_missing' for e in denials)):
            alert('closure_missing_required_outputs', tid, {'missing_keys': missing})
        old = [e.id for e in exceptions if e.task_id == tid and e.status == 'open' and
               (clock(as_of) - clock(e.last_seen_at)).total_seconds() >= THRESHOLDS['stalled_exception_seconds_at_least']]
        if old:
            stalled.append(tid)
            alert('stalled_unresolved_exceptions', tid, {'incident_ids': sorted(old)})
        owner = view['ownership']['session']
        if row['state'] in ACTIVE and (owner is None or owner['ended_at'] is not None):
            orphans.append(tid)
    return {'schema_version': 1, 'as_of': as_of, 'event_cursor': event_cursor,
        'project_ids': sorted(ids), 'task_id': ident, 'selection': 'exact_task' if ident else 'all_project_tasks',
        'window': 'all_persisted_history', 'thresholds': dict(THRESHOLDS),
        'counts': {'tasks': len(selected), 'completed_tasks': sum(n['state'] == 'completed' for n in selected),
            'terminal_tasks': sum(n['state'] not in ACTIVE for n in selected),
            'child_creation_events': len(creations), 'delegation_records_including_reparent': delegation_count,
            'governance_denials': len(denials), 'delegation_denials_observed': len(delegation_denials),
            'unclassified_legacy_denials': sum('request_type' not in e.details for e in denials),
            'contract_initializations': len(revisions) - len(amendments), 'amendment_successes': len(amendments),
            'amendment_requests_observed': len(amendments) + len(amendment_denials),
            'canonical_exceptions': len(exceptions), 'unresolved_exceptions': sum(e.status == 'open' for e in exceptions),
            'exception_recorded_events': sum(e.event_type == 'exception.recorded' for e in relevant),
            'exception_inspections': sum(e.event_type == 'exception.inspected' for e in relevant),
            'remediation_attempt_records': len(remediations),
            'authorized_retries': sum(r.status == 'authorized' for r in remediations),
            'deliverables': sum(len(v['deliverables']) for v in recoveries),
            'validated_deliverables': sum(d['validated'] for v in recoveries for d in v['deliverables']),
            'tasks_with_requirements': sum(v['readiness']['has_requirements'] for v in recoveries),
            'tasks_with_requirements_satisfied': sum(v['readiness']['has_requirements'] and v['readiness']['requirements_satisfied'] for v in recoveries)},
        'provenance': {
            'historical_child_creations_by_root_at_creation': [{'root_id': root, 'count': historical[root]} for root in sorted(historical)],
            'historical_root_unavailable_count': sum(root is None for _, root, _ in creations),
            'creation_parent_unavailable_count': sum(e.event_type == 'task.created' and 'parent_id' not in e.details for e in relevant),
            'tasks_without_creation_event_count': len(selected_ids - {e.task_id for e in relevant if e.event_type == 'task.created'}),
            'current_children_by_current_root': [{'root_id': root, 'count': current_roots[root]} for root in sorted(current_roots)],
            'source': 'recorded_creation_and_parent_change_events; current_projection_separate'},
        'coverage': {'request_counts': 'success_and_persisted_policy_denial_only; validation/auth/stale_requests_not_recorded',
            'exception_recurrence': unavailable('canonical_deduplication_does_not_record_repeat_occurrences'),
            'original_root_without_recorded_ancestry': unavailable('mutable_current_lineage_is_not_historical_evidence')},
        'budgets': [{'task_id': v['task']['id'], **v['remaining_budget']} for v in recoveries],
        'orphan_task_ids': sorted(orphans), 'stalled_task_ids': sorted(stalled),
        'definitions': {'orphan': 'active task with no registered owner or ended owner; not a liveness claim',
            'stalled': 'open task_exception last_seen_at at least 3600 seconds old; includes paused/terminal tasks',
            'readiness': 'latest nonsuperseded exact revision has latest matching passed evidence; not execution completion'},
        'runtime': {'source': 'persisted_agent_sessions',
            'observations': [{'task_id': v['task']['id'], **v['runtime']} for v in recoveries],
            'context_utilization': unavailable('no_authoritative_context_telemetry')},
        'alerts': sorted(alerts, key=lambda a: (a['task_id'], a['code']))}
