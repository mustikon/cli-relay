from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli_relay import config as config_mod

from .conftest import fake_command

pytest.importorskip("mcp")

from cli_relay.mcp_server import build_server


def make_config(tmp_path: Path, fixture: str) -> config_mod.Config:
    return config_mod.parse(
        {
            "order": ["claude"],
            "relay": {"state_path": str(tmp_path / "state.json")},
            "providers": {"claude": {"command": fake_command(fixture)}},
        }
    )


def payload(result: object) -> dict[str, object]:
    # mcp 2.x: CallToolResult with .structured_content / .content
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured.get("result", structured)  # type: ignore[return-value]
    if hasattr(result, "content") and not isinstance(result, tuple):
        result = result.content
    # mcp 1.x: (content_blocks, structured) for dict-returning tools
    if isinstance(result, tuple):
        result = result[1] if isinstance(result[1], dict) else result[0]
    if isinstance(result, dict):
        return result.get("result", result)  # type: ignore[return-value]
    first = next(iter(result))  # type: ignore[call-overload]
    return json.loads(first.text)  # type: ignore[no-any-return]


async def test_tools_listed_and_callable(tmp_path: Path) -> None:
    server = build_server(make_config(tmp_path, "claude_success.ndjson"))
    names = {tool.name for tool in await server.list_tools()}
    assert names == {"relay_run", "relay_status"}
    result = payload(await server.call_tool("relay_run", {"prompt": "hello"}))
    assert result["ok"] is True and result["provider"] == "claude"
    assert result["text"] == "The secret word is banana."
    status = payload(await server.call_tool("relay_status", {}))
    assert status["order"] == ["claude"]


async def test_mcp_permission_is_capped(tmp_path: Path) -> None:
    cfg = make_config(tmp_path, "claude_success.ndjson")
    server = build_server(cfg)
    result = payload(await server.call_tool("relay_run", {"prompt": "x", "permission": "full"}))
    assert result["permission"] == "read_only"

    raised = config_mod.parse(
        {
            "order": ["claude"],
            "relay": {"state_path": str(tmp_path / "state.json")},
            "mcp": {"max_permission": "edit"},
            "providers": {"claude": {"command": fake_command("claude_success.ndjson")}},
        }
    )
    result = payload(
        await build_server(raised).call_tool("relay_run", {"prompt": "x", "permission": "full"})
    )
    assert result["permission"] == "edit"
