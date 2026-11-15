"""Google Antigravity CLI (``agy -p --output-format stream-json``).

``agy`` is a native executable, so the prompt can go on the command line. Very long
prompts are written to a temp file that the agent is asked to read (Windows limits a
command line to ~32k characters).
"""

from __future__ import annotations

import os
from typing import Any, ClassVar

from cli_relay.adapters.base import CliAdapter, Invocation, ParseState, short
from cli_relay.errors import classify, reset_hint
from cli_relay.events import Completed, Event, Failed, Started, TextDelta, ToolActivity
from cli_relay.models import Permission, Task

_MODE = {
    Permission.READ_ONLY: "plan",
    Permission.EDIT: "accept-edits",
    Permission.FULL: "auto",
}
_MAX_ARG_PROMPT = 20_000


class AntigravityAdapter(CliAdapter):
    type_name: ClassVar[str] = "antigravity"
    default_command: ClassVar[list[str]] = ["agy"]

    def build(self, task: Task, exe: list[str]) -> Invocation:
        inv = Invocation(argv=[*exe])
        prompt = task.prompt
        if task.system_prompt:
            prompt = f"{task.system_prompt.strip()}\n\n---\n\n{prompt}"
        if len(prompt) > _MAX_ARG_PROMPT:
            path = self.temp_text_file(inv, prompt)
            inv.argv += ["--add-dir", os.path.dirname(path)]
            prompt = (
                f"Your full task is in the file {path}. Read that file completely and "
                f"do exactly what it says."
            )
        inv.argv += [
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--mode",
            _MODE[self.permission_for(task)],
        ]
        if self.settings.model:
            inv.argv += ["--model", self.settings.model]
        if session := task.resume.get(self.name):
            inv.argv += ["--conversation", session]
        inv.argv += self.settings.extra_args
        return inv

    def parse(self, obj: dict[str, Any], state: ParseState) -> list[Event]:
        p = self.name
        event = obj.get("event")
        if event == "init":
            init = obj.get("init") or {}
            state.session_id = obj.get("conversation_id") or init.get("conversation_id")
            state.model = init.get("model")
            return [Started(provider=p, session_id=state.session_id, model=state.model)]
        if event == "step_update":
            step = obj.get("step_update") or {}
            kind = step.get("step_type")
            if kind == "agent_response" and step.get("text_delta"):
                return [TextDelta(provider=p, text=step["text_delta"])]
            if kind == "tool" and step.get("state") == "ACTIVE":
                info = step.get("tool_info") or {}
                params = info.get("parameters") or {}
                detail = next((short(v) for v in params.values() if isinstance(v, str)), "")
                return [
                    ToolActivity(provider=p, tool=str(step.get("tool_name", "?")), detail=detail)
                ]
            if step.get("error"):
                state.error_text.append(short(step["error"], 500))
            return []
        if event == "error" or "error" in obj:
            err = obj.get("error")
            state.error_text.append(short(err if err is not None else obj, 500))
            return []
        if event == "result":
            return [self._result(obj.get("result") or {}, state)]
        return []

    def _result(self, res: dict[str, Any], state: ParseState) -> Event:
        session = res.get("conversation_id") or state.session_id
        status = str(res.get("status", ""))
        response = res.get("response") if isinstance(res.get("response"), str) else ""
        if status.upper() == "SUCCESS":
            return Completed(
                provider=self.name,
                text=(response or "").strip(),
                session_id=session,
                usage=res.get("usage") or {},
                duration_s=res.get("duration_seconds"),
            )
        message = "\n".join(
            x for x in (status, str(res.get("error") or ""), response or "", *state.error_text) if x
        )
        return Failed(
            provider=self.name,
            kind=classify(message),
            message=short(message, 2000),
            resets_at=reset_hint(message),
            session_id=session,
        )
