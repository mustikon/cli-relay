"""The relay: try providers in order, switch on limits, brief the next one."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from cli_relay.adapters.base import Adapter
from cli_relay.events import (
    Completed,
    Event,
    Failed,
    FailureKind,
    Skipped,
    Switched,
    Waiting,
)
from cli_relay.handoff import HandoffLog
from cli_relay.models import Task
from cli_relay.quota import QuotaTracker

RELAY = "relay"


@dataclass
class RelayPolicy:
    #: Failure kinds that make the relay move on to the next provider.
    switch_on: frozenset[FailureKind] = field(
        default_factory=lambda: frozenset(
            {
                FailureKind.RATE_LIMIT,
                FailureKind.AUTH,
                FailureKind.NOT_INSTALLED,
                FailureKind.ERROR,
            }
        )
    )
    #: If a blocked provider resets within this many seconds, wait for it instead of
    #: skipping it. ``0`` disables waiting.
    wait_if_reset_within_s: float = 0
    #: Write HANDOFF.md into the task's working directory when switching mid-task.
    write_handoff_file: bool = False


class Relay:
    def __init__(
        self,
        adapters: Sequence[Adapter],
        quota: QuotaTracker | None = None,
        policy: RelayPolicy | None = None,
    ) -> None:
        if not adapters:
            raise ValueError("at least one provider is required")
        self.adapters = list(adapters)
        self.quota = quota or QuotaTracker()
        self.policy = policy or RelayPolicy()

    async def run(self, task: Task) -> AsyncIterator[Event]:
        """Stream events from the first provider that finishes the task.

        The stream ends with the provider's :class:`Completed` event, or with a
        :class:`Failed` event from provider ``"relay"`` when every provider failed.
        """
        if problem := task.check_files():
            # the same bad path would fail on every provider: stop before the first
            yield Failed(provider="relay", kind=FailureKind.ERROR, message=problem)
            return
        log = HandoffLog(task.prompt)
        previous: str | None = None
        last_failure: Failed | None = None
        for adapter in self.adapters:
            ok, reason = adapter.available()
            if not ok:
                yield Skipped(provider=adapter.name, reason=reason)
                continue
            block = self.quota.blocked(adapter.name)
            if block:
                wait = block.until - time.time()
                if 0 < wait <= self.policy.wait_if_reset_within_s:
                    yield Waiting(provider=adapter.name, until=block.until, reason=block.reason)
                    await asyncio.sleep(wait)
                else:
                    yield Skipped(
                        provider=adapter.name,
                        reason=f"limited until {_clock(block.until)}: {block.reason}",
                    )
                    continue
            if previous is not None:
                yield Switched(
                    provider=previous,
                    to_provider=adapter.name,
                    reason=last_failure.kind.value if last_failure else "",
                )
                if self.policy.write_handoff_file and task.cwd and log.has_progress:
                    log.write(task.cwd)
            attempt = task if previous is None else task.with_prompt(log.continuation_prompt())
            result: Event | None = None
            async for event in adapter.run(attempt):
                self.quota.observe(event)
                log.observe(event)
                yield event
                if event.terminal:
                    result = event
            if isinstance(result, Completed):
                return
            last_failure = (
                result
                if isinstance(result, Failed)
                else Failed(
                    provider=adapter.name, kind=FailureKind.ERROR, message="no terminal event"
                )
            )
            if last_failure.kind not in self.policy.switch_on:
                yield Failed(
                    provider=RELAY,
                    kind=last_failure.kind,
                    message=f"{adapter.name}: {last_failure.message}",
                )
                return
            previous = adapter.name
        yield Failed(
            provider=RELAY,
            kind=last_failure.kind if last_failure else FailureKind.NOT_INSTALLED,
            message="no provider could complete the task"
            + (
                f"; last error from {last_failure.provider}: {last_failure.message}"
                if last_failure
                else "; none was available"
            ),
        )

    async def run_to_end(self, task: Task) -> tuple[Event, list[Event]]:
        """Convenience wrapper: return ``(terminal_event, all_events)``."""
        events = [event async for event in self.run(task)]
        return events[-1], events


def _clock(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))
