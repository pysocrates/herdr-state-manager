"""Observational reconciliation; no task transitions or runtime commands."""
import fcntl
from contextlib import contextmanager
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.engine import make_url
from .database import transaction
from .models import Project, Pane, AgentSession, Incident, AdapterHealth, Task, now
from .services import Service, Problem
from .adapters.base import NoopAdapter
from .adapters.herdr import HerdrAdapter
from .adapters.hermes import HermesAdapter


@contextmanager
def reconcile_lock(database_url):
    path = Path(make_url(database_url).database + '.reconcile.lock')
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Problem(409, 'reconcile_running', 'Another reconciliation is running')
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def reconcile(engine, config, herdr=None, hermes=None):
    with reconcile_lock(config.database_url):
        with transaction(engine) as db:
            projects = [(p.id, p.configuration) for p in db.scalars(select(Project).where(Project.enabled.is_(True)))]
            panes = [(p.id, p.project_id, p.external_pane_id) for p in db.scalars(select(Pane).where(Pane.terminated_at.is_(None)))]
            agents = [(a.id, a.project_id, a.external_session_id) for a in db.scalars(select(AgentSession).where(AgentSession.ended_at.is_(None)))]
        import os
        if os.getenv('HSM_RUNTIME_ADAPTER', 'noop') not in {'herdr', 'noop'}:
            raise Problem(422, 'adapter_config', 'Unknown HSM_RUNTIME_ADAPTER')
        herdr = herdr or (HerdrAdapter() if os.getenv('HSM_RUNTIME_ADAPTER', 'noop') == 'herdr' else NoopAdapter())
        pane_observations = herdr.observe([p[2] for p in panes])
        sessions = {}
        for project, settings in projects:
            adapter = hermes or HermesAdapter(settings.get('hermes_metadata_database'))
            sessions[project] = adapter.observe([a[2] for a in agents if a[1] == project and a[2]])
        summary = {'unregistered_herdr_panes': pane_observations.unregistered_count if pane_observations.available else None, 'projects': len(projects), 'observed': 0, 'incidents_opened': 0, 'incidents_resolved': 0}
        with transaction(engine, True) as db:
            principal = {'identity': 'system:reconciler', 'role': 'operator', 'projects': ['*'], 'actions': ['*']}
            service = Service(db, principal)
            from .approvals import expire
            expire(service)
            def incident(project, kind, resource, discrepancy, can_resolve=True):
                item = db.scalar(select(Incident).where(Incident.project_id == project,
                                 Incident.incident_type == kind, Incident.resource_id == resource))
                stamp = now()
                if discrepancy:
                    if item is None:
                        item = Incident(project_id=project, incident_type=kind, resource_id=resource)
                        db.add(item)
                        db.flush()
                    elif item.status == 'open':
                        item.last_seen_at = stamp
                        return
                    item.status, item.first_seen_at, item.last_seen_at, item.resolved_at = 'open', stamp, stamp, None
                    service.event(project, 'incident.opened', details={'incident_id': item.id, 'type': kind, 'resource_id': resource})
                    summary['incidents_opened'] += 1
                elif item and item.status == 'open' and can_resolve:
                    item.status, item.last_seen_at, item.resolved_at = 'resolved', stamp, stamp
                    service.event(project, 'incident.resolved', details={'incident_id': item.id})
                    summary['incidents_resolved'] += 1
            for project, _ in projects:
                for provider, observations in [('herdr', pane_observations), ('hermes', sessions[project])]:
                    health = db.scalar(select(AdapterHealth).where(AdapterHealth.project_id == project, AdapterHealth.provider == provider))
                    if health is None:
                        health = AdapterHealth(project_id=project, provider=provider, status='unknown')
                        db.add(health)
                    health.status = 'available' if observations.available else 'unavailable'
                    health.observed_at = now()
                    incident(project, 'adapter_unavailable', provider, not observations.available)
            for model, registrations in [(Pane, panes), (AgentSession, agents)]:
                for ident, project, external in registrations:
                    item = db.get(model, ident)
                    # Re-check against live registration after bounded external calls.
                    if item is None or (getattr(item, 'terminated_at', None) or getattr(item, 'ended_at', None)):
                        continue
                    current_external = item.external_pane_id if model is Pane else item.external_session_id
                    if current_external != external:
                        continue
                    observations = pane_observations if model is Pane else sessions[project]
                    item.last_observed_at = now()
                    observation = observations.resources.get(external) if external else None
                    if not observations.available:
                        item.runtime_status = 'unavailable'
                        continue  # Existing missing incident is not falsely resolved.
                    if external is None:
                        item.runtime_status = 'unknown'
                        continue
                    item.runtime_status = observation.status if observation else 'missing'
                    if observation and model is AgentSession:
                        item.telemetry = observation.telemetry
                    incident(project, 'pane_missing' if model is Pane else 'session_missing', ident, observation is None)
                    from datetime import datetime, timezone
                    heartbeat = item.heartbeat_at
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(heartbeat.replace('Z', '+00:00'))).total_seconds() if heartbeat else None
                    incident(project, 'heartbeat_stale', ident, age is not None and age > 900)
                    if model is Pane:
                        task = db.get(Task, item.task_id) if item.task_id else None
                        incident(project, 'pane_unassigned', ident, item.task_id is None)
                        incident(project, 'pane_on_terminal_task', ident, task is not None and task.state in {'completed', 'failed', 'cancelled'})
                    # Compare only confirmed IDs; do not rewrite ownership/correlation.
                    if observation and model is Pane and observation.external_session_id and item.agent_session_id:
                        agent = db.get(AgentSession, item.agent_session_id)
                        if agent and agent.external_session_id:
                            incident(project, 'session_mismatch', ident, agent.external_session_id != observation.external_session_id)
                    elif observation and model is Pane:
                        incident(project, 'session_mismatch', ident, False)
                    summary['observed'] += 1
        return summary
