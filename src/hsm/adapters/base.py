from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Observation:
    status: str
    telemetry: dict = field(default_factory=dict)
    external_session_id: str | None = None


@dataclass
class Observations:
    available: bool
    resources: dict[str, Observation] = field(default_factory=dict)
    unregistered_count: int = 0


class RuntimeAdapter(Protocol):
    def observe(self, identifiers: list[str]) -> Observations: ...


class NoopAdapter:
    def observe(self, identifiers: list[str]) -> Observations:
        return Observations(False)
