"""Native approval telemetry, deliberately independent of HSM authorization grants."""
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from .models import ApprovalObservation, now
from .services import Problem, serialize

Digest = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class ObservationCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    event_id: UUID
    phase: Literal['pre', 'post']
    surface: Literal['cli', 'gateway', 'smart']
    # Only a hash of a genuine upstream request ID, never command/session heuristics.
    native_request_id: Digest | None = None
    session_key_hash: Digest | None = None
    command_hash: Digest | None = None
    choice: Literal['once', 'session', 'always', 'deny', 'timeout', 'smart_approve', 'smart_deny'] | None = None

    @model_validator(mode='after')
    def phase_choice(self):
        if (self.phase == 'post') != (self.choice is not None):
            raise ValueError('Only post observations require a choice')
        return self


def ingest(service, data):
    data = ObservationCreate.model_validate(data).model_dump(mode='json')
    service.project(data['project_id'], 'approvals:observe')
    # Durable event receipt independent of the normal 24-hour request replay cache.
    event_id = str(data['event_id'])
    from .models import ApprovalObservationReceipt
    import hashlib
    import json
    canonical = dict(data, event_id=event_id)
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
    receipt_key = hashlib.sha256(json.dumps([service.principal['identity'], event_id]).encode()).hexdigest()
    receipt = service.db.get(ApprovalObservationReceipt, receipt_key)
    if receipt:
        if receipt.digest != digest:
            raise Problem(409, 'observation_event_conflict', 'Observation event ID reused with different data')
        return serialize(service.db.get(ApprovalObservation, receipt.observation_id))
    native = data.get('native_request_id')
    correlation_key = native or event_id
    item = service.db.scalar(select(ApprovalObservation).where(
        ApprovalObservation.project_id == data['project_id'],
        ApprovalObservation.reporter == service.principal['identity'],
        ApprovalObservation.correlation_key == correlation_key))
    if item is None:
        item = ApprovalObservation(project_id=data['project_id'], reporter=service.principal['identity'],
            correlation_key=correlation_key, correlation='native_request_id' if native else 'missing_request_id',
            native_request_id=native, surface=data['surface'], session_key_hash=data.get('session_key_hash'),
            command_hash=data.get('command_hash'), status='pending')
        service.db.add(item)
        service.db.flush()
    else:
        for key in ('surface', 'session_key_hash', 'command_hash'):
            if getattr(item, key) != data.get(key):
                raise Problem(409, 'observation_identity_conflict', 'Request metadata changed')
    changed = False
    if data['phase'] == 'post':
        if item.choice is not None and item.choice != data['choice']:
            raise Problem(409, 'observation_final', 'Observed final decisions are immutable')
        if item.choice is None:
            item.choice, item.status, item.responded_at = data['choice'], 'outcome', now()
            changed = True
    elif item.requested_at is None:
        item.requested_at = now()
        changed = True
    service.db.add(ApprovalObservationReceipt(id=receipt_key, digest=digest, observation_id=item.id))
    if changed:
        service.event(item.project_id, 'approval.observed', details={'observation_id': item.id, 'phase': data['phase']})
    service.db.flush()
    return serialize(item)
