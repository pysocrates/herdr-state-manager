from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def uid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = 'projects'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str]
    configuration: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[str] = mapped_column(default=now)


class Task(Base):
    __tablename__ = 'tasks'
    __table_args__ = (UniqueConstraint('project_id', 'task_key'),)
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id'), index=True)
    task_key: Mapped[str]
    title: Mapped[str]
    objective: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(default='queued', index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey('tasks.id'))
    owner_session_id: Mapped[str | None] = mapped_column(ForeignKey('agent_sessions.id'))
    repository_path: Mapped[str | None]
    worktree_path: Mapped[str | None]
    base_commit: Mapped[str | None]
    created_at: Mapped[str] = mapped_column(default=now)
    updated_at: Mapped[str] = mapped_column(default=now)
    completed_at: Mapped[str | None]
    version: Mapped[int] = mapped_column(default=1)


class AgentSession(Base):
    __tablename__ = 'agent_sessions'
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id'), index=True)
    profile: Mapped[str]
    external_session_id: Mapped[str | None]
    started_at: Mapped[str] = mapped_column(default=now)
    ended_at: Mapped[str | None]
    last_observed_at: Mapped[str | None]
    heartbeat_at: Mapped[str | None]
    runtime_status: Mapped[str] = mapped_column(default='unknown')
    telemetry: Mapped[dict] = mapped_column(JSON, default=dict)


class Pane(Base):
    __tablename__ = 'panes'
    __table_args__ = (UniqueConstraint('project_id', 'provider', 'external_pane_id'),)
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id'), index=True)
    provider: Mapped[str] = mapped_column(default='herdr')
    external_pane_id: Mapped[str]
    task_id: Mapped[str | None] = mapped_column(ForeignKey('tasks.id'))
    agent_session_id: Mapped[str | None] = mapped_column(ForeignKey('agent_sessions.id'))
    runtime_status: Mapped[str] = mapped_column(default='unknown')
    created_at: Mapped[str] = mapped_column(default=now)
    terminated_at: Mapped[str | None]
    last_observed_at: Mapped[str | None]
    heartbeat_at: Mapped[str | None]


class Event(Base):
    __tablename__ = 'events'
    __table_args__ = {'sqlite_autoincrement': True}
    cursor: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(default=uid, unique=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id'), index=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey('tasks.id'), index=True)
    timestamp: Mapped[str] = mapped_column(default=now)
    event_type: Mapped[str]
    actor: Mapped[str]
    previous_state: Mapped[str | None]
    new_state: Mapped[str | None]
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class Idempotency(Base):
    __tablename__ = 'idempotency'
    scope: Mapped[str] = mapped_column(primary_key=True)
    digest: Mapped[str]
    expires_at: Mapped[str]
    response: Mapped[dict] = mapped_column(JSON)
    status: Mapped[int]


class Incident(Base):
    __tablename__ = 'incidents'
    __table_args__ = (UniqueConstraint('project_id', 'incident_type', 'resource_id'),)
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id'), index=True)
    incident_type: Mapped[str]
    resource_id: Mapped[str]
    first_seen_at: Mapped[str] = mapped_column(default=now)
    last_seen_at: Mapped[str] = mapped_column(default=now)
    status: Mapped[str] = mapped_column(default='open')
    resolved_at: Mapped[str | None]


class AdapterHealth(Base):
    __tablename__ = 'adapter_health'
    __table_args__ = (UniqueConstraint('project_id', 'provider'),)
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id'), index=True)
    provider: Mapped[str]
    status: Mapped[str]
    observed_at: Mapped[str] = mapped_column(default=now)


class Approval(Base):
    __tablename__ = 'approvals'
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id'), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), index=True)
    requester: Mapped[str]
    operation: Mapped[str]
    target: Mapped[str]
    action: Mapped[str] = mapped_column(Text)
    artifact: Mapped[str]
    status: Mapped[str] = mapped_column(default='pending')
    requested_at: Mapped[str] = mapped_column(default=now)
    expires_at: Mapped[str]
    resolved_at: Mapped[str | None]
    operator: Mapped[str | None]
    resolution_note: Mapped[str | None]
    consumed_at: Mapped[str | None]
    consumed_by: Mapped[str | None]
    version: Mapped[int] = mapped_column(default=1)
