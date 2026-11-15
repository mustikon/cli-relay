from __future__ import annotations

import time
from collections.abc import Iterable
from pathlib import Path

import pytest

from cli_relay.events import (
    Completed,
    Event,
    Failed,
    FailureKind,
    QuotaInfo,
    Skipped,
    Started,
    Switched,
    TextDelta,
    ToolActivity,
    Waiting,
)
from cli_relay.models import Task
from cli_relay.quota import QuotaTracker
from cli_relay.relay import Relay, RelayPolicy

from .conftest import ScriptedAdapter


def ok(text: str = "done") -> object:
    def events(name: str) -> Iterable[Event]:
        yield Started(provider=name, session_id=f"{name}-1")
        yield TextDelta(provider=name, text=text)
        yield Completed(provider=name, text=text, session_id=f"{name}-1")

    return events


def limited(resets_in: float = 3600, with_progress: bool = True) -> object:
    def events(name: str) -> Iterable[Event]:
        yield Started(provider=name)
        if with_progress:
            yield ToolActivity(provider=name, tool="Edit", detail="src/app.py")
            yield TextDelta(provider=name, text="half of the work")
        yield Failed(
            provider=name,
            kind=FailureKind.RATE_LIMIT,
            message="usage limit reached",
            resets_at=time.time() + resets_in,
        )

    return events


def failing(kind: FailureKind) -> object:
    def events(name: str) -> Iterable[Event]:
        yield Failed(provider=name, kind=kind, message=f"{kind.value} happened")

    return events


async def run(relay: Relay, prompt: str = "fix the bug") -> list[Event]:
    return [e async for e in relay.run(Task(prompt=prompt))]


async def test_first_provider_wins(state_file: Path) -> None:
    a, b = ScriptedAdapter("a", ok("A")), ScriptedAdapter("b", ok("B"))  # type: ignore[arg-type]
    events = await run(Relay([a, b], QuotaTracker(state_file)))
    assert isinstance(events[-1], Completed) and events[-1].provider == "a"
    assert b.tasks == []


async def test_switches_on_rate_limit_and_hands_off(state_file: Path) -> None:
    a = ScriptedAdapter("claude", limited())  # type: ignore[arg-type]
    b = ScriptedAdapter("antigravity", ok("finished"))  # type: ignore[arg-type]
    quota = QuotaTracker(state_file)
    events = await run(Relay([a, b], quota))
    switched = [e for e in events if isinstance(e, Switched)]
    assert len(switched) == 1
    assert (switched[0].provider, switched[0].to_provider) == ("claude", "antigravity")
    assert switched[0].reason == "rate_limit"
    assert isinstance(events[-1], Completed) and events[-1].provider == "antigravity"
    handed = b.tasks[0].prompt
    assert "fix the bug" in handed and "src/app.py" in handed and "half of the work" in handed
    assert "git status" in handed
    # the limit is remembered for the next run, which now skips claude
    assert quota.blocked("claude") is not None
    a2 = ScriptedAdapter("claude", ok())  # type: ignore[arg-type]
    b2 = ScriptedAdapter("antigravity", ok())  # type: ignore[arg-type]
    events = await run(Relay([a2, b2], QuotaTracker(state_file)))
    assert isinstance(events[0], Skipped) and events[0].provider == "claude"
    assert a2.tasks == []


async def test_handoff_without_progress_keeps_original_prompt(state_file: Path) -> None:
    a = ScriptedAdapter("a", limited(with_progress=False))  # type: ignore[arg-type]
    b = ScriptedAdapter("b", ok())  # type: ignore[arg-type]
    await run(Relay([a, b], QuotaTracker(state_file)), prompt="just this")
    assert b.tasks[0].prompt == "just this"


async def test_waits_when_reset_is_close(state_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    quota = QuotaTracker(state_file)
    quota.block("a", time.time() + 60, "five_hour")
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("cli_relay.relay.asyncio.sleep", fake_sleep)
    a = ScriptedAdapter("a", ok())  # type: ignore[arg-type]
    events = await run(Relay([a], quota, RelayPolicy(wait_if_reset_within_s=120)))
    assert isinstance(events[0], Waiting)
    assert slept and 0 < slept[0] <= 60
    assert isinstance(events[-1], Completed)


async def test_non_switching_failure_stops(state_file: Path) -> None:
    a = ScriptedAdapter("a", failing(FailureKind.TIMEOUT))  # type: ignore[arg-type]
    b = ScriptedAdapter("b", ok())  # type: ignore[arg-type]
    events = await run(Relay([a, b], QuotaTracker(state_file)))
    last = events[-1]
    assert isinstance(last, Failed) and last.provider == "relay"
    assert last.kind is FailureKind.TIMEOUT
    assert b.tasks == []


async def test_everything_fails(state_file: Path) -> None:
    a = ScriptedAdapter("a", failing(FailureKind.AUTH))  # type: ignore[arg-type]
    b = ScriptedAdapter("b", ok(), available=(False, "not installed"))  # type: ignore[arg-type]
    events = await run(Relay([a, b], QuotaTracker(state_file)))
    assert any(isinstance(e, Skipped) and e.provider == "b" for e in events)
    last = events[-1]
    assert isinstance(last, Failed) and last.provider == "relay"
    assert "auth happened" in last.message


async def test_high_utilization_is_skipped_preemptively(state_file: Path) -> None:
    quota = QuotaTracker(state_file, skip_above_utilization=0.95)
    quota.observe(
        QuotaInfo(
            provider="a",
            window="five_hour",
            status="allowed",
            utilization=0.97,
            resets_at=time.time() + 600,
        )
    )
    a = ScriptedAdapter("a", ok())  # type: ignore[arg-type]
    b = ScriptedAdapter("b", ok())  # type: ignore[arg-type]
    events = await run(Relay([a, b], quota))
    assert isinstance(events[0], Skipped) and "97%" in events[0].reason
    assert events[-1].provider == "b"


async def test_writes_handoff_file(state_file: Path, tmp_path: Path) -> None:
    a = ScriptedAdapter("a", limited())  # type: ignore[arg-type]
    b = ScriptedAdapter("b", ok())  # type: ignore[arg-type]
    relay = Relay([a, b], QuotaTracker(state_file), RelayPolicy(write_handoff_file=True))
    _ = [e async for e in relay.run(Task(prompt="task", cwd=tmp_path))]
    assert "src/app.py" in (tmp_path / "HANDOFF.md").read_text(encoding="utf-8")


def test_relay_needs_providers() -> None:
    with pytest.raises(ValueError):
        Relay([])
