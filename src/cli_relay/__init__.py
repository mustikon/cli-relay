"""cli-relay: one worker interface over several coding-agent CLIs, with fallback on limits."""

from cli_relay.events import (
    Completed,
    Event,
    Failed,
    FailureKind,
    QuotaInfo,
    Skipped,
    Started,
    Switched,
    TextDelta,
    ToolActivity,
    Waiting,
)
from cli_relay.models import Permission, ProviderSettings, Task
from cli_relay.relay import Relay, RelayPolicy

__version__ = "0.2.0"

__all__ = [
    "Completed",
    "Event",
    "Failed",
    "FailureKind",
    "Permission",
    "ProviderSettings",
    "QuotaInfo",
    "Relay",
    "RelayPolicy",
    "Skipped",
    "Started",
    "Switched",
    "Task",
    "TextDelta",
    "ToolActivity",
    "Waiting",
    "__version__",
]
