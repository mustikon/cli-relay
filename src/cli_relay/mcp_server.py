"""Expose the relay as an MCP server (stdio), so any MCP client - Claude Code, Codex,
Antigravity, a voice assistant - can delegate a task and get fallback for free.

Requires the optional dependency: ``pip install "cli-relay[mcp]"``.
"""

# No ``from __future__ import annotations`` here: FastMCP inspects the tool signatures at
# runtime and must see the real ``Context`` class, which is imported lazily below.

import contextlib
from pathlib import Path
from typing import Any

from cli_relay.config import Config
from cli_relay.events import Completed, Failed, Skipped, Started, Switched
from cli_relay.models import Permission, Task
from cli_relay.relay import Relay


def build_server(config: Config) -> Any:
    try:
        from mcp.server.fastmcp import Context, FastMCP
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise SystemExit(
            "cli-relay mcp needs the optional dependency: pip install 'cli-relay[mcp]'"
        ) from exc

    server = FastMCP("cli-relay")

    @server.tool()
    async def relay_run(
        prompt: str,
        cwd: str | None = None,
        providers: list[str] | None = None,
        permission: str = "read_only",
        ctx: Context[Any, Any, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a coding task on the first available agent CLI (Claude Code, Antigravity,
        Codex, API...), switching to the next one if a provider hits its usage limit.

        permission: read_only (default), edit, or full. The server caps it at the
        configured ``[mcp] max_permission`` (read_only unless the user raised it).
        Returns which provider finished, its final answer and any switches that happened.
        """
        granted = Permission(permission).capped_by(config.mcp_max_permission)
        relay = Relay(config.adapters(providers), config.quota(), config.policy)
        task = Task(prompt=prompt, cwd=Path(cwd) if cwd else None, permission=granted)
        switches: list[str] = []
        skipped: list[str] = []
        final: Completed | Failed | None = None
        async for event in relay.run(task):
            if isinstance(event, Switched):
                switches.append(f"{event.provider} -> {event.to_provider} ({event.reason})")
            elif isinstance(event, Skipped):
                skipped.append(f"{event.provider}: {event.reason}")
            elif isinstance(event, Started) and ctx is not None:
                with contextlib.suppress(Exception):  # no request context when called directly
                    await ctx.info(f"{event.provider} started")
            elif isinstance(event, Completed | Failed):
                final = event
        if isinstance(final, Completed):
            return {
                "ok": True,
                "provider": final.provider,
                "text": final.text,
                "session_id": final.session_id,
                "permission": granted.value,
                "switches": switches,
                "skipped": skipped,
            }
        return {
            "ok": False,
            "error": final.message if final else "no result",
            "kind": final.kind.value if isinstance(final, Failed) else None,
            "switches": switches,
            "skipped": skipped,
        }

    @server.tool()
    def relay_status() -> dict[str, Any]:
        """Show which agent providers are installed, and which are rate-limited and until when."""
        quota = config.quota()
        out = []
        for adapter in config.adapters():
            ok, reason = adapter.available()
            block = quota.blocked(adapter.name)
            out.append(
                {
                    "provider": adapter.name,
                    "available": ok,
                    "reason": reason,
                    "blocked_until": block.until if block else None,
                    "blocked_reason": block.reason if block else None,
                }
            )
        return {"order": config.order, "providers": out}

    return server


def serve(config: Config) -> None:
    build_server(config).run()
