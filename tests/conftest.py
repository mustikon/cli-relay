from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Callable, Iterable
from pathlib import Path
from typing import Any, ClassVar

import pytest

from cli_relay.adapters.base import Adapter, CliAdapter, ParseState
from cli_relay.events import Event
from cli_relay.models import ProviderSettings, Task
from cli_relay.ndjson import parse_line

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
FAKE_CLI = HERE / "fakes" / "fake_cli.py"


def fixture_path(name: str) -> Path:
    return FIXTURES / name


def fake_command(fixture: str, *extra: str) -> list[str]:
    """A provider ``command`` that replays a fixture instead of running a real CLI."""
    target = "-" if fixture == "-" else str(fixture_path(fixture))
    return [sys.executable, str(FAKE_CLI), target, *extra]


def parse_fixture(adapter: CliAdapter, name: str) -> tuple[list[Event], ParseState]:
    state = ParseState()
    events: list[Event] = []
    for line in fixture_path(name).read_text(encoding="utf-8").splitlines():
        for obj in parse_line(line):
            events.extend(adapter.parse(obj, state))
    return events, state


class ScriptedAdapter(Adapter):
    """In-memory adapter: yields a fixed list of events and records the tasks it got."""

    type_name: ClassVar[str] = "scripted"

    def __init__(
        self,
        name: str,
        events: Callable[[str], Iterable[Event]],
        available: tuple[bool, str] = (True, ""),
    ) -> None:
        super().__init__(ProviderSettings(name=name, type="scripted"))
        self._events = events
        self._available = available
        self.tasks: list[Task] = []

    def available(self) -> tuple[bool, str]:
        return self._available

    async def run(self, task: Task) -> AsyncIterator[Event]:
        self.tasks.append(task)
        for event in self._events(self.name):
            yield event


@pytest.fixture
def state_file(tmp_path: Path) -> Path:
    return tmp_path / "state.json"


def as_types(events: Iterable[Any]) -> list[str]:
    return [e.type for e in events]
