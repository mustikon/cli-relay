"""Carry a half-finished task over to the next provider."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from cli_relay.events import Event, Failed, TextDelta, ToolActivity

HANDOFF_FILE = "HANDOFF.md"


@dataclass
class HandoffLog:
    """Collects what each provider did, to brief the next one."""

    original_prompt: str
    max_tools: int = 40
    max_text_chars: int = 6000
    attempts: list[tuple[str, str]] = field(default_factory=list)  # (provider, reason)
    tools: deque[str] = field(default_factory=deque)
    text: str = ""

    def observe(self, event: Event) -> None:
        if isinstance(event, ToolActivity):
            self.tools.append(f"[{event.provider}] {event.tool} {event.detail}".rstrip())
            while len(self.tools) > self.max_tools:
                self.tools.popleft()
        elif isinstance(event, TextDelta):
            self.text = (self.text + event.text)[-self.max_text_chars :]
        elif isinstance(event, Failed):
            self.attempts.append((event.provider, f"{event.kind.value}: {event.message[:200]}"))

    @property
    def has_progress(self) -> bool:
        return bool(self.tools or self.text.strip())

    def render(self) -> str:
        lines = ["# Handoff", "", "## Original task", "", self.original_prompt.strip(), ""]
        if self.attempts:
            lines += ["## Previous attempts", ""]
            lines += [f"- {p} stopped: {r}" for p, r in self.attempts]
            lines.append("")
        if self.tools:
            lines += ["## Actions already taken (most recent last)", ""]
            lines += [f"- {t}" for t in self.tools]
            lines.append("")
        if self.text.strip():
            lines += ["## Last output of the previous agent", "", self.text.strip(), ""]
        return "\n".join(lines)

    def continuation_prompt(self) -> str:
        if not self.has_progress:
            return self.original_prompt
        return (
            "You are taking over a task from another coding agent that had to stop "
            "(for example because it hit a usage limit). Its handoff notes are below.\n"
            "The previous agent may already have changed files: before doing anything else, "
            "inspect the current state of the working directory (for example `git status` "
            "and `git diff`). Do not redo work that is already done; finish the task.\n\n"
            + self.render()
        )

    def write(self, directory: Path) -> Path:
        path = directory / HANDOFF_FILE
        path.write_text(self.render(), encoding="utf-8")
        return path
