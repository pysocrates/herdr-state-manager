import json
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator

Text = Annotated[str, Field(min_length=1, max_length=4096)]
Slug = Annotated[str, Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')]
Identifier = Annotated[str, Field(max_length=200)]
Runtime = Literal['alive', 'idle', 'working', 'blocked', 'done', 'missing', 'terminated', 'unknown', 'unavailable']


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    @field_validator('parent_id', 'owner_session_id', 'task_id', 'agent_session_id', check_fields=False)
    @classmethod
    def canonical_uuid(cls, value):
        return str(UUID(value)) if value is not None else None


class ProjectCreate(Input):
    id: Slug
    name: Text
    configuration: dict = Field(default_factory=dict)

    @field_validator('configuration')
    @classmethod
    def bounded(cls, value):
        if len(json.dumps(value).encode()) > 8192:
            raise ValueError('Metadata exceeds 8 KiB')
        return value


class TaskCreate(Input):
    project_id: Slug
    task_key: Annotated[str, Field(min_length=1, max_length=100)]
    title: Text
    objective: Text
    parent_id: str | None = None
    owner_session_id: str | None = None
    repository_path: Text | None = None
    worktree_path: Text | None = None
    base_commit: Identifier | None = None


class TaskPatch(Input):
    expected_version: Annotated[int, Field(ge=1)]
    title: Text | None = None
    objective: Text | None = None
    parent_id: str | None = None
    owner_session_id: str | None = None
    repository_path: Text | None = None
    worktree_path: Text | None = None
    base_commit: Identifier | None = None


class Transition(Input):
    expected_version: Annotated[int, Field(ge=1)]
    state: Literal['queued', 'planning', 'running', 'awaiting_approval', 'blocked', 'review', 'completed', 'failed', 'cancelled']


class SessionCreate(Input):
    project_id: Slug
    profile: Annotated[str, Field(min_length=1, max_length=200)]
    external_session_id: Identifier | None = None


class PaneCreate(Input):
    project_id: Slug
    provider: Literal['herdr', 'noop'] = 'herdr'
    external_pane_id: Annotated[str, Field(min_length=1, max_length=200)]
    task_id: str | None = None
    agent_session_id: str | None = None


class RuntimePatch(Input):
    runtime_status: Runtime | None = None
    task_id: str | None = None
    agent_session_id: str | None = None


class Empty(Input):
    pass


class ApprovalCreate(Input):
    project_id: Slug
    task_id: Annotated[str, Field(min_length=36, max_length=36)]
    operation: Annotated[str, Field(min_length=1, max_length=200)]
    target: Text
    action: Text
    artifact: Annotated[str, Field(min_length=1, max_length=512)]
    expires_at: Annotated[str, Field(max_length=40)]

    @field_validator('expires_at')
    @classmethod
    def expiry(cls, value):
        from datetime import datetime, timezone
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('Expiration requires timezone')
        return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


class ApprovalResolve(Input):
    expected_version: Annotated[int, Field(ge=1)]
    decision: Literal['approved', 'rejected']
    note: Text | None = None


class ApprovalConsume(Input):
    expected_version: Annotated[int, Field(ge=1)]
    task_id: Annotated[str, Field(min_length=36, max_length=36)]
    operation: Annotated[str, Field(min_length=1, max_length=200)]
    target: Text
    action: Text
    artifact: Annotated[str, Field(min_length=1, max_length=512)]
