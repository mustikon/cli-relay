"""Normalized event stream shared by every provider adapter.

Every adapter run yields zero or more progress events and then exactly one terminal
event: :class:`Completed` or :class:`Failed`. The relay adds its own routing events
(:class:`Skipped`, :class:`Waiting`, :class:`Switched`).
"""

from __future__ import annotations

import enum
import time
from dataclasses import asdict, dataclass, field
from typing import Any


class FailureKind(enum.StrEnum):
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    NOT_INSTALLED = "not_installed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True, kw_only=True)
class Event:
    provider: str
    at: float = field(default_factory=time.time)

    @property
    def type(self) -> str:
        name = type(self).__name__
        return "".join("_" + c.lower() if c.isupper() else c for c in name).lstrip("_")

    @property
    def terminal(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, enum.Enum):
                data[key] = value.value
        return {"type": self.type, **data}


@dataclass(frozen=True, kw_only=True)
class Started(Event):
    session_id: str | None = None
    model: str | None = None


@dataclass(frozen=True, kw_only=True)
class TextDelta(Event):
    text: str


@dataclass(frozen=True, kw_only=True)
class ToolActivity(Event):
    tool: str
    detail: str = ""


@dataclass(frozen=True, kw_only=True)
class QuotaInfo(Event):
    """A usage window reported by the provider (e.g. Claude's five_hour / seven_day)."""

    window: str
    status: str
    utilization: float | None = None
    resets_at: float | None = None


@dataclass(frozen=True, kw_only=True)
class Completed(Event):
    text: str
    session_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    cost_usd: float | None = None
    duration_s: float | None = None

    @property
    def terminal(self) -> bool:
        return True


@dataclass(frozen=True, kw_only=True)
class Failed(Event):
    kind: FailureKind
    message: str = ""
    resets_at: float | None = None
    session_id: str | None = None

    @property
    def terminal(self) -> bool:
        return True


@dataclass(frozen=True, kw_only=True)
class Skipped(Event):
    reason: str


@dataclass(frozen=True, kw_only=True)
class Waiting(Event):
    until: float
    reason: str = ""


@dataclass(frozen=True, kw_only=True)
class Switched(Event):
    """Emitted by the relay; ``provider`` is the one being left."""

    to_provider: str
    reason: str
