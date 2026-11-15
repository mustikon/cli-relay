"""Remembers which providers are rate-limited and until when, across runs.

State is a small JSON file (see :func:`default_state_path`). Writes are atomic; concurrent
cli-relay processes may overwrite each other's newest observation, which is acceptable
for a single-user tool.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cli_relay.events import Event, Failed, FailureKind, QuotaInfo


def default_state_path() -> Path:
    if env := os.environ.get("CLI_RELAY_STATE"):
        return Path(env)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "cli-relay" / "state.json"


@dataclass(frozen=True)
class Block:
    until: float
    reason: str


class QuotaTracker:
    def __init__(
        self,
        path: Path | None = None,
        *,
        default_cooldown_s: float = 1800,
        skip_above_utilization: float | None = 0.98,
    ) -> None:
        self.path = path
        self.default_cooldown_s = default_cooldown_s
        self.skip_above_utilization = skip_above_utilization
        self._data: dict[str, dict[str, Any]] = self._load()

    # -- persistence -------------------------------------------------------------------
    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path or not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def _entry(self, provider: str) -> dict[str, Any]:
        return self._data.setdefault(provider, {})

    # -- observation -------------------------------------------------------------------
    def observe(self, event: Event) -> None:
        if isinstance(event, QuotaInfo):
            windows = self._entry(event.provider).setdefault("windows", {})
            windows[event.window] = {
                "status": event.status,
                "utilization": event.utilization,
                "resets_at": event.resets_at,
                "seen_at": event.at,
            }
            self._save()
        elif isinstance(event, Failed) and event.kind is FailureKind.RATE_LIMIT:
            until = event.resets_at or (event.at + self.default_cooldown_s)
            self.block(event.provider, until, event.message[:300] or "rate limited")

    def block(self, provider: str, until: float, reason: str) -> None:
        self._entry(provider)["blocked"] = {"until": until, "reason": reason}
        self._save()

    def clear(self, provider: str | None = None) -> None:
        if provider is None:
            self._data.clear()
        else:
            self._data.pop(provider, None)
        self._save()

    # -- queries -----------------------------------------------------------------------
    def blocked(self, provider: str, now: float | None = None) -> Block | None:
        now = time.time() if now is None else now
        entry = self._data.get(provider, {})
        blocked = entry.get("blocked")
        if blocked and float(blocked.get("until", 0)) > now:
            return Block(float(blocked["until"]), str(blocked.get("reason", "")))
        limit = self.skip_above_utilization
        for name, window in (entry.get("windows") or {}).items():
            resets = window.get("resets_at")
            if not resets or float(resets) <= now:
                continue
            util = window.get("utilization")
            exhausted = window.get("status") not in (None, "allowed", "allowed_warning")
            if exhausted or (limit is not None and util is not None and float(util) >= limit):
                return Block(
                    float(resets),
                    f"{name} window at {util:.0%}"
                    if util is not None
                    else f"{name} window {window.get('status')}",
                )
        return None

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return json.loads(json.dumps(self._data))  # type: ignore[no-any-return]
