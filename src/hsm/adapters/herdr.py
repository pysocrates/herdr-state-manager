"""Herdr 0.9.1 protocol 22 schema. Fixed read-only CLI, no shell."""
import json
import subprocess
import tempfile
from pydantic import BaseModel, Field, ValidationError
from .base import Observation, Observations


class SessionReference(BaseModel):
    agent: str
    kind: str
    value: str = Field(max_length=200)


class PaneInfo(BaseModel):
    pane_id: str = Field(min_length=1, max_length=200)
    agent_status: str
    agent_session: SessionReference | None = None


class Snapshot(BaseModel):
    protocol: int
    panes: list[PaneInfo]


class HerdrAdapter:
    def __init__(self, executable='herdr', timeout=5):
        self.executable, self.timeout = executable, timeout

    def observe(self, identifiers):
        try:
            # File output bounds memory consumption. Runtime output is never logged.
            with tempfile.TemporaryFile() as output:
                result = subprocess.run([self.executable, 'api', 'snapshot'], stdout=output,
                                        stderr=subprocess.DEVNULL, timeout=self.timeout, check=False)
                if result.returncode or output.tell() > 4 * 1024 * 1024:
                    return Observations(False)
                output.seek(0)
                raw = json.load(output)
            # API response envelope is verified in exported SuccessResponse schema.
            payload = raw.get('result', raw)
            if isinstance(payload, dict) and payload.get('type') == 'session_snapshot':
                payload = payload['snapshot']
            snapshot = Snapshot.model_validate(payload)
            if snapshot.protocol != 22:
                return Observations(False)
            resources = {}
            for pane in snapshot.panes:
                if pane.pane_id not in identifiers:
                    continue
                status = pane.agent_status if pane.agent_status in {'idle', 'working', 'blocked', 'done', 'unknown'} else 'unknown'
                ref = pane.agent_session
                external = ref.value if ref and ref.agent == 'hermes' and ref.kind == 'id' else None
                resources[pane.pane_id] = Observation(status, external_session_id=external)
            return Observations(True, resources, sum(p.pane_id not in identifiers for p in snapshot.panes))
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, AttributeError, ValidationError):
            return Observations(False)
