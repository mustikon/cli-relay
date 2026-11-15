"""Adapter interface and the shared implementation for NDJSON-streaming CLIs."""

from __future__ import annotations

import abc
import contextlib
import os
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, ClassVar

from cli_relay.errors import classify
from cli_relay.events import Event, Failed, FailureKind
from cli_relay.models import Permission, ProviderSettings, Task
from cli_relay.ndjson import parse_line
from cli_relay.proc import ProcessRun, UnsafeArgumentError, resolve


class Adapter(abc.ABC):
    """A provider the relay can hand a task to."""

    #: Adapter type name used in config files (``type = "claude"``).
    type_name: ClassVar[str]

    def __init__(self, settings: ProviderSettings) -> None:
        self.settings = settings

    @property
    def name(self) -> str:
        return self.settings.name

    @abc.abstractmethod
    def available(self) -> tuple[bool, str]:
        """``(True, "")`` if the provider can be used right now, else ``(False, reason)``."""

    @abc.abstractmethod
    def run(self, task: Task) -> AsyncIterator[Event]:
        """Yield progress events, then exactly one terminal event."""

    def permission_for(self, task: Task) -> Permission:
        return task.permission.capped_by(self.settings.permission)


@dataclass
class Invocation:
    argv: list[str]
    stdin: bytes | None = None
    temp_files: list[str] = field(default_factory=list)


@dataclass
class ParseState:
    """Mutable per-run state an adapter's parser can use."""

    session_id: str | None = None
    model: str | None = None
    text: list[str] = field(default_factory=list)
    error_text: list[str] = field(default_factory=list)
    resets_at: float | None = None
    terminal: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class CliAdapter(Adapter):
    """Base for adapters that launch a CLI emitting one JSON event per line."""

    default_command: ClassVar[list[str]]

    def command(self) -> list[str] | None:
        return resolve(self.settings.command or self.default_command)

    def available(self) -> tuple[bool, str]:
        if self.command() is None:
            wanted = (self.settings.command or self.default_command)[0]
            return False, f"'{wanted}' not found on PATH"
        return True, ""

    # -- to implement -------------------------------------------------------------------
    @abc.abstractmethod
    def build(self, task: Task, exe: list[str]) -> Invocation: ...

    @abc.abstractmethod
    def parse(self, obj: dict[str, Any], state: ParseState) -> list[Event]: ...

    # -- shared ------------------------------------------------------------------------
    def finish(self, state: ParseState, returncode: int | None, stderr: str) -> Event:
        """Terminal event when the stream ended without one."""
        message = "\n".join(state.error_text) or stderr.strip()[-2000:]
        if not message:
            message = f"{self.name} exited with code {returncode} without a result"
        return Failed(
            provider=self.name,
            kind=classify(message),
            message=message,
            resets_at=state.resets_at,
            session_id=state.session_id,
        )

    def temp_text_file(self, inv: Invocation, text: str, suffix: str = ".md") -> str:
        fd, path = tempfile.mkstemp(prefix="cli-relay-", suffix=suffix)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        inv.temp_files.append(path)
        return path

    async def run(self, task: Task) -> AsyncIterator[Event]:
        exe = self.command()
        if exe is None:
            yield Failed(
                provider=self.name, kind=FailureKind.NOT_INSTALLED, message=self.available()[1]
            )
            return
        inv = self.build(task, exe)
        state = ParseState()
        try:
            try:
                proc = ProcessRun(
                    inv.argv,
                    cwd=task.cwd,
                    stdin_data=inv.stdin,
                    env=self.settings.env or None,
                    timeout_s=task.timeout_s,
                )
            except UnsafeArgumentError as exc:
                yield Failed(provider=self.name, kind=FailureKind.ERROR, message=str(exc))
                return
            async for line in proc.lines():
                for obj in parse_line(line):
                    for event in self.parse(obj, state):
                        if state.terminal and event.terminal:
                            continue  # keep the first terminal event only
                        if event.terminal:
                            state.terminal = True
                        yield event
            if proc.timed_out and not state.terminal:
                yield Failed(
                    provider=self.name,
                    kind=FailureKind.TIMEOUT,
                    message=f"no result within {task.timeout_s}s",
                    session_id=state.session_id,
                )
            elif not state.terminal:
                yield self.finish(state, proc.returncode, proc.stderr_tail)
        finally:
            for path in inv.temp_files:
                with contextlib.suppress(OSError):
                    os.unlink(path)


def short(value: object, limit: int = 120) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
