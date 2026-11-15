"""OpenAI Codex CLI (``codex exec --json``). EXPERIMENTAL.

Built from the published JSONL event documentation; not yet verified against a live
``codex`` install. The prompt always goes on stdin (``codex`` is an npm shim on Windows).
"""

from __future__ import annotations

from typing import Any, ClassVar

from cli_relay.adapters.base import CliAdapter, Invocation, ParseState, short
from cli_relay.errors import classify, reset_hint
from cli_relay.events import Completed, Event, Failed, Started, TextDelta, ToolActivity
from cli_relay.models import Permission, Task

_SANDBOX = {
    Permission.READ_ONLY: ["--sandbox", "read-only"],
    Permission.EDIT: ["--sandbox", "workspace-write"],
    Permission.FULL: ["--sandbox", "workspace-write", "--approve-for-me"],
}
_TOOL_ITEMS = {
    "command_execution": "command",
    "file_change": "path",
    "mcp_tool_call": "tool",
    "web_search": "query",
}


class CodexAdapter(CliAdapter):
    type_name: ClassVar[str] = "codex"
    default_command: ClassVar[list[str]] = ["codex"]

    def build(self, task: Task, exe: list[str]) -> Invocation:
        argv = [*exe, "exec"]
        if session := task.resume.get(self.name):
            argv += ["resume", session]
        argv += ["--json", "--skip-git-repo-check", *_SANDBOX[self.permission_for(task)]]
        if self.settings.model:
            argv += ["--model", self.settings.model]
        argv += self.settings.extra_args
        argv.append("-")  # read the prompt from stdin
        prompt = task.prompt
        if task.system_prompt:
            prompt = f"{task.system_prompt.strip()}\n\n---\n\n{prompt}"
        return Invocation(argv=argv, stdin=prompt.encode("utf-8"))

    def parse(self, obj: dict[str, Any], state: ParseState) -> list[Event]:
        p = self.name
        kind = obj.get("type")
        if kind == "thread.started":
            state.session_id = obj.get("thread_id")
            return [Started(provider=p, session_id=state.session_id)]
        if kind in ("item.started", "item.completed"):
            item = obj.get("item") or {}
            itype = item.get("type")
            if itype == "agent_message" and kind == "item.completed":
                text = str(item.get("text", ""))
                state.text.append(text)
                return [TextDelta(provider=p, text=text)]
            if itype in _TOOL_ITEMS and kind == "item.started":
                detail = item.get(_TOOL_ITEMS[itype]) or item.get("changes") or ""
                return [ToolActivity(provider=p, tool=str(itype), detail=short(detail))]
            return []
        if kind == "error":
            state.error_text.append(short(obj.get("message") or obj, 500))
            return []
        if kind == "turn.failed":
            err = obj.get("error") or {}
            message = "\n".join(
                [
                    str(err.get("message", err)) if isinstance(err, dict) else str(err),
                    *state.error_text,
                ]
            )
            return [
                Failed(
                    provider=p,
                    kind=classify(message),
                    message=short(message, 2000),
                    resets_at=reset_hint(message),
                    session_id=state.session_id,
                )
            ]
        if kind == "turn.completed":
            return [
                Completed(
                    provider=p,
                    text=state.text[-1] if state.text else "",
                    session_id=state.session_id,
                    usage=obj.get("usage") or {},
                )
            ]
        return []
