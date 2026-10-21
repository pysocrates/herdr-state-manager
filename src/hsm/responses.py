"""Pydantic validation at the public response boundary."""
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class Pagination(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    has_more: bool


class Envelope(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    data: dict[str, Any] | list[dict[str, Any]]
    pagination: Pagination | None = None


class Snapshot(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: int
    project: str | None
    generated_at: str
    revision: int
    projects: list[dict]
    active_tasks: list[dict]
    task_hierarchy: dict
    agent_sessions: list[dict]
    active_panes: list[dict]
    related_ended_sessions: list[dict]
    related_terminated_panes: list[dict]
    pending_approvals: list[dict]
    outstanding_approvals: list[dict]
    blocked_tasks: list[dict]
    unresolved_incidents: list[dict]
    adapter_health: list[dict]
    recent_events: list[dict]
    warnings: list[str]
    truncation: dict[str, bool]
    governance: dict
