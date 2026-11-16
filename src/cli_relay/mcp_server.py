"""Expose the relay as an MCP server (stdio), so any MCP client - Claude Code, Codex,
Antigravity, a voice assistant - can delegate a task and get fallback for free.

Requires the optional dependency: ``pip install "cli-relay[mcp]"``. Works with the MCP
Python SDK 1.x (``FastMCP``) and 2.x (``MCPServer``).
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from cli_relay.config import Config
from cli_relay.events import Completed, Failed, Skipped, Switched
from cli_relay.models import Permission, Task
from cli_relay.relay import Relay


def _server_class() -> Any:
    """``MCPServer`` from mcp 2.x, else ``FastMCP`` from mcp 1.x."""
    for module, name in (("mcp.server.mcpserver", "MCPServer"), ("mcp.server.fastmcp", "FastMCP")):
        try:
            return getattr(importlib.import_module(module), name)
        except (ImportError, AttributeError):
            continue
    try:
        import mcp  # noqa: F401
    except ImportError:
        raise SystemExit(
            "cli-relay mcp needs the optional dependency: pip install 'cli-relay[mcp]'"
        ) from None
    raise SystemExit(
        "cli-relay mcp: the installed 'mcp' package has neither MCPServer (2.x) nor FastMCP "
        "(1.x); install a supported version: pip install 'cli-relay[mcp]'"
    )


def build_server(config: Config) -> Any:
    server = _server_class()("cli-relay")

    @server.tool()  # type: ignore[untyped-decorator,misc,unused-ignore]
    async def relay_run(
        prompt: str,
        cwd: str | None = None,
        providers: list[str] | None = None,
        permission: str = "read_only",
        images: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run a coding task on the first available agent CLI (Claude Code, Antigravity,
        Codex, API...), switching to the next one if a provider hits its usage limit.

        permission: read_only (default), edit, or full. The server caps it at the
        configured ``[mcp] max_permission`` (read_only unless the user raised it).
        images: paths of image files (png, jpg, gif, webp) to show the agent; they are
        sent to the provider that runs the task.
        Returns which provider finished, its final answer and any switches that happened.
        """
        granted = Permission(permission).capped_by(config.mcp_max_permission)
        relay = Relay(config.adapters(providers), config.quota(), config.policy)
        task = Task(
            prompt=prompt,
            cwd=Path(cwd) if cwd else None,
            permission=granted,
            images=tuple(Path(p) for p in images or ()),
        )
        switches: list[str] = []
        skipped: list[str] = []
        final: Completed | Failed | None = None
        async for event in relay.run(task):
            if isinstance(event, Switched):
                switches.append(f"{event.provider} -> {event.to_provider} ({event.reason})")
            elif isinstance(event, Skipped):
                skipped.append(f"{event.provider}: {event.reason}")
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

    @server.tool()  # type: ignore[untyped-decorator,misc,unused-ignore]
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
    build_server(config).run(transport="stdio")
