"""Claude Code (``claude -p --output-format stream-json``).

Runs the official, unmodified ``claude`` binary under the user's own login. The prompt is
sent on stdin and the system prompt through a temp file, so no user text ever goes on
the command line (``claude`` is an npm ``.cmd`` shim on Windows).
"""

from __future__ import annotations

from typing import Any, ClassVar

from cli_relay.adapters.base import CliAdapter, Invocation, ParseState, short
from cli_relay.errors import classify, reset_hint
from cli_relay.events import (
    Completed,
    Event,
    Failed,
    FailureKind,
    QuotaInfo,
    Started,
    TextDelta,
    ToolActivity,
)
from cli_relay.models import Permission, Task

_PERMISSION_MODE = {
    Permission.READ_ONLY: "dontAsk",  # pre-approved reads only, everything else denied
    Permission.EDIT: "acceptEdits",
    Permission.FULL: "auto",
}


class ClaudeAdapter(CliAdapter):
    type_name: ClassVar[str] = "claude"
    default_command: ClassVar[list[str]] = ["claude"]

    def build(self, task: Task, exe: list[str]) -> Invocation:
        argv = [
            *exe,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--permission-mode",
            _PERMISSION_MODE[self.permission_for(task)],
            "--permission-prompts",
            "none",
        ]
        inv = Invocation(argv=argv, stdin=task.prompt.encode("utf-8"))
        if self.settings.model:
            argv += ["--model", self.settings.model]
        if session := task.resume.get(self.name):
            argv += ["--resume", session]
        if task.system_prompt:
            argv += ["--append-system-prompt-file", self.temp_text_file(inv, task.system_prompt)]
        argv += self.settings.extra_args
        return inv

    def parse(self, obj: dict[str, Any], state: ParseState) -> list[Event]:
        kind = obj.get("type")
        p = self.name
        if kind == "system":
            sub = obj.get("subtype")
            if sub == "init":
                state.session_id = obj.get("session_id")
                state.model = obj.get("model")
                return [Started(provider=p, session_id=state.session_id, model=state.model)]
            if sub == "api_retry":
                err = obj.get("error")
                if err:
                    state.error_text.append(short(err, 500))
            return []
        if kind == "stream_event":
            ev = obj.get("event") or {}
            delta = ev.get("delta") or {}
            if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                state.extra["partial"] = True
                return [TextDelta(provider=p, text=delta.get("text", ""))]
            return []
        if kind == "assistant":
            out: list[Event] = []
            for block in (obj.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    out.append(
                        ToolActivity(
                            provider=p,
                            tool=str(block.get("name", "?")),
                            detail=_tool_detail(block.get("input")),
                        )
                    )
                elif block.get("type") == "text" and not state.extra.get("partial"):
                    out.append(TextDelta(provider=p, text=block.get("text", "")))
            return out
        if kind == "rate_limit_event":
            return self._quota(obj.get("rate_limit_info") or {}, state)
        if kind == "result":
            return [self._result(obj, state)]
        return []

    def _quota(self, info: dict[str, Any], state: ParseState) -> list[Event]:
        events: list[Event] = []
        windows = info.get("unifiedWindows") or {}
        if not windows and info.get("rateLimitType"):
            windows = {info["rateLimitType"]: info}
        for window, data in windows.items():
            if not isinstance(data, dict):
                continue
            status = str(data.get("status", "unknown"))
            resets = _num(data.get("resetsAt"))
            if status not in ("allowed", "allowed_warning") and resets:
                state.resets_at = max(state.resets_at or 0, resets)
            events.append(
                QuotaInfo(
                    provider=self.name,
                    window=str(window),
                    status=status,
                    utilization=_num(data.get("utilization")),
                    resets_at=resets,
                )
            )
        return events

    def _result(self, obj: dict[str, Any], state: ParseState) -> Event:
        session = obj.get("session_id") or state.session_id
        text = obj.get("result")
        if obj.get("subtype") == "success" and not obj.get("is_error"):
            duration = _num(obj.get("duration_ms"))
            return Completed(
                provider=self.name,
                text=text if isinstance(text, str) else "",
                session_id=session,
                usage=obj.get("usage") or {},
                cost_usd=_num(obj.get("total_cost_usd")),
                duration_s=duration / 1000 if duration is not None else None,
            )
        message = (
            "\n".join(str(x) for x in (text, *obj.get("errors", []), *state.error_text) if x)
            or f"claude result: {obj.get('subtype')}"
        )
        status = obj.get("api_error_status")
        failure = FailureKind.RATE_LIMIT if status in (429, "429") else classify(message, status)
        if failure is FailureKind.ERROR and state.resets_at:
            failure = FailureKind.RATE_LIMIT  # a window was reported as exhausted
        return Failed(
            provider=self.name,
            kind=failure,
            message=short(message, 2000),
            resets_at=state.resets_at or reset_hint(message),
            session_id=session,
        )


def _tool_detail(data: object) -> str:
    if isinstance(data, dict):
        for key in ("command", "file_path", "path", "pattern", "url", "query", "description"):
            if data.get(key):
                return short(data[key])
    return ""


def _num(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None
