"""Load ``cli-relay.toml``.

Lookup order: ``--config PATH``, ``$CLI_RELAY_CONFIG``, ``./cli-relay.toml``, then the user
config dir (``%APPDATA%/cli-relay/config.toml`` or ``~/.config/cli-relay/config.toml``).
Without a file, the default chain is Claude Code -> Antigravity -> Codex.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cli_relay.adapters import Adapter, create
from cli_relay.events import FailureKind
from cli_relay.models import Permission, ProviderSettings
from cli_relay.quota import QuotaTracker, default_state_path
from cli_relay.relay import RelayPolicy

DEFAULT_ORDER = ["claude", "antigravity", "codex"]


def user_config_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "cli-relay" / "config.toml"


def find_config(explicit: str | Path | None = None) -> Path | None:
    if explicit:
        if not Path(explicit).is_file():
            raise FileNotFoundError(f"config file not found: {explicit}")
        return Path(explicit)
    candidates = [
        os.environ.get("CLI_RELAY_CONFIG"),
        Path("cli-relay.toml"),
        user_config_path(),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


@dataclass
class Config:
    order: list[str]
    providers: dict[str, ProviderSettings]
    policy: RelayPolicy = field(default_factory=RelayPolicy)
    state_path: Path | None = None
    default_cooldown_s: float = 1800
    skip_above_utilization: float | None = 0.98
    #: Highest permission an MCP client may request through ``relay_run``. Defaults to
    #: read-only so a sandboxed agent cannot escalate by delegating to another one.
    mcp_max_permission: Permission = Permission.READ_ONLY
    source: Path | None = None

    def adapters(self, only: list[str] | None = None) -> list[Adapter]:
        names = only or self.order
        unknown = [n for n in names if n not in self.providers]
        if unknown:
            raise ValueError(
                f"unknown provider(s): {', '.join(unknown)}; "
                f"configured: {', '.join(self.providers)}"
            )
        return [create(self.providers[n]) for n in names]

    def quota(self) -> QuotaTracker:
        return QuotaTracker(
            self.state_path or default_state_path(),
            default_cooldown_s=self.default_cooldown_s,
            skip_above_utilization=self.skip_above_utilization,
        )


def load(path: str | Path | None = None) -> Config:
    source = find_config(path)
    data: dict[str, Any] = {}
    if source:
        with source.open("rb") as fh:
            data = tomllib.load(fh)
    return parse(data, source)


def parse(data: dict[str, Any], source: Path | None = None) -> Config:
    providers: dict[str, ProviderSettings] = {
        name: ProviderSettings(name=name, type=name) for name in DEFAULT_ORDER
    }
    for name, raw in (data.get("providers") or {}).items():
        if not isinstance(raw, dict):
            raise ValueError(f"[providers.{name}] must be a table")
        permission = raw.get("permission")
        command = raw.get("command")
        if isinstance(command, str):
            command = [command]
        providers[name] = ProviderSettings(
            name=name,
            type=str(raw.get("type", name)),
            command=list(command) if command else None,
            model=raw.get("model"),
            permission=Permission(permission) if permission else None,
            extra_args=[str(a) for a in raw.get("extra_args", [])],
            env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
            base_url=raw.get("base_url"),
            api_key_env=raw.get("api_key_env"),
        )
    relay = data.get("relay") or {}
    policy = RelayPolicy(
        wait_if_reset_within_s=float(relay.get("wait_if_reset_within_s", 0)),
        write_handoff_file=bool(relay.get("write_handoff_file", False)),
    )
    if "switch_on" in relay:
        policy.switch_on = frozenset(FailureKind(k) for k in relay["switch_on"])
    state = relay.get("state_path")
    skip = relay.get("skip_above_utilization", 0.98)
    return Config(
        order=list(data.get("order") or DEFAULT_ORDER),
        providers=providers,
        policy=policy,
        state_path=Path(state).expanduser() if state else None,
        default_cooldown_s=float(relay.get("default_cooldown_s", 1800)),
        skip_above_utilization=None if skip in (None, False) else float(skip),
        mcp_max_permission=Permission(
            (data.get("mcp") or {}).get("max_permission", Permission.READ_ONLY.value)
        ),
        source=source,
    )
