from __future__ import annotations

import time

from cli_relay.adapters import AntigravityAdapter, ClaudeAdapter, CodexAdapter
from cli_relay.errors import classify, reset_hint
from cli_relay.events import (
    Completed,
    Failed,
    FailureKind,
    QuotaInfo,
    Started,
    TextDelta,
    ToolActivity,
)
from cli_relay.models import ProviderSettings
from cli_relay.ndjson import parse_line

from .conftest import as_types, parse_fixture


def claude() -> ClaudeAdapter:
    return ClaudeAdapter(ProviderSettings(name="claude", type="claude"))


def test_ndjson_plain_and_salvage() -> None:
    assert parse_line('{"a": 1}') == [{"a": 1}]
    assert parse_line("   ") == []
    assert parse_line("[1, 2]") == []
    assert parse_line('junk {"a": 1} more {"b": {"c": 2}} tail') == [{"a": 1}, {"b": {"c": 2}}]
    # an object spliced into the middle of another one: the inner object survives
    spliced = '{"type":"result","x":{"type":"rate_limit_event","n":1}'
    assert {"type": "rate_limit_event", "n": 1} in parse_line(spliced)


def test_claude_success_stream() -> None:
    events, state = parse_fixture(claude(), "claude_success.ndjson")
    assert as_types(events) == [
        "started",
        "tool_activity",
        "text_delta",
        "text_delta",
        "quota_info",
        "quota_info",
        "completed",
    ]
    started = events[0]
    assert isinstance(started, Started) and started.model == "claude-haiku-5-5"
    tool = events[1]
    assert isinstance(tool, ToolActivity) and tool.tool == "Read"
    assert tool.detail.endswith("note.txt")
    # with partial messages on, the full assistant text block is not repeated
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert text == "The secret word is banana."
    quota = [e for e in events if isinstance(e, QuotaInfo)]
    assert {q.window: q.utilization for q in quota} == {"five_hour": 0.04, "seven_day": 0.42}
    done = events[-1]
    assert isinstance(done, Completed)
    assert done.text == "The secret word is banana."
    assert done.session_id == "2aff023c-19fb-46ef-86b2-712257b4b519"
    assert done.cost_usd == 0.00075 and done.duration_s == 4.2
    assert state.resets_at is None


def test_claude_rate_limited() -> None:
    events, _ = parse_fixture(claude(), "claude_rate_limited.ndjson")
    failed = events[-1]
    assert isinstance(failed, Failed)
    assert failed.kind is FailureKind.RATE_LIMIT
    assert failed.resets_at == 1794743400
    assert "hit your limit" in failed.message


def test_claude_without_partial_messages_uses_assistant_text() -> None:
    adapter = claude()
    from cli_relay.adapters.base import ParseState

    state = ParseState()
    out = adapter.parse(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}}, state
    )
    assert [e.text for e in out if isinstance(e, TextDelta)] == ["hello"]


def test_claude_garbled_stream_recovers() -> None:
    events, _ = parse_fixture(claude(), "garbled.ndjson")
    assert as_types(events) == ["started", "text_delta", "quota_info"]


def test_antigravity_success() -> None:
    adapter = AntigravityAdapter(ProviderSettings(name="antigravity", type="antigravity"))
    events, _ = parse_fixture(adapter, "agy_success.ndjson")
    assert as_types(events) == ["started", "tool_activity", "text_delta", "text_delta", "completed"]
    done = events[-1]
    assert isinstance(done, Completed)
    assert done.text == "ok"
    assert done.session_id == "9255eb39-ebf3-4f1f-a8f3-55e9e0334d45"


def test_antigravity_quota_failure() -> None:
    adapter = AntigravityAdapter(ProviderSettings(name="antigravity", type="antigravity"))
    before = time.time()
    events, _ = parse_fixture(adapter, "agy_quota.ndjson")
    failed = events[-1]
    assert isinstance(failed, Failed) and failed.kind is FailureKind.RATE_LIMIT
    assert failed.resets_at is not None
    assert 2 * 3600 + 5 * 60 - 5 <= failed.resets_at - before <= 2 * 3600 + 5 * 60 + 5


def test_codex_success_and_failure() -> None:
    adapter = CodexAdapter(ProviderSettings(name="codex", type="codex"))
    events, _ = parse_fixture(adapter, "codex_success.ndjson")
    assert as_types(events) == ["started", "tool_activity", "text_delta", "completed"]
    done = events[-1]
    assert isinstance(done, Completed) and done.text.startswith("Repo contains")
    assert done.usage["output_tokens"] == 122

    events, _ = parse_fixture(adapter, "codex_failed.ndjson")
    failed = events[-1]
    assert isinstance(failed, Failed) and failed.kind is FailureKind.RATE_LIMIT
    assert failed.resets_at is not None


def test_codex_real_rate_limit_stream() -> None:
    """Captured from codex-cli 0.167 on a ChatGPT plan that had hit its limit."""
    adapter = CodexAdapter(ProviderSettings(name="codex", type="codex"))
    events, _ = parse_fixture(adapter, "codex_rate_limited.ndjson")
    # config warnings arrive as `item.completed` items of type "error" and are ignored
    assert as_types(events) == ["started", "failed"]
    failed = events[-1]
    assert isinstance(failed, Failed) and failed.kind is FailureKind.RATE_LIMIT
    assert failed.session_id == "01a1de50-3c76-7f03-b0a9-94d771900716"
    assert failed.message.count("hit your usage limit") == 1
    assert failed.resets_at is not None  # "try again at 11:05 AM"
    assert time.localtime(failed.resets_at)[3:5] == (11, 5)


def test_classify_and_reset_hint() -> None:
    assert classify("Error: 429 Too Many Requests") is FailureKind.RATE_LIMIT
    assert classify("RESOURCE_EXHAUSTED") is FailureKind.RATE_LIMIT
    assert classify("Invalid API key provided") is FailureKind.AUTH
    assert classify("Please log in first") is FailureKind.AUTH
    assert classify("segfault") is FailureKind.ERROR
    now = 1_000_000.0
    assert reset_hint("try again in 2 hours", now) == now + 7200
    assert reset_hint("retry in 1h 30m", now) == now + 5400
    assert reset_hint("resets after 15 minutes", now) == now + 900
    assert reset_hint("found in 5 more files", now) is None
    assert reset_hint("limit|1794743400", 1_700_000_000) == 1794743400


def test_reset_hint_wall_clock() -> None:
    base = time.mktime((2026, 11, 15, 9, 0, 0, 0, 0, -1))  # 09:00 local

    def hm(epoch: float | None) -> tuple[int, int, int]:
        assert epoch is not None
        t = time.localtime(epoch)
        return t.tm_mday, t.tm_hour, t.tm_min

    assert hm(reset_hint("try again at 11:05 AM.", base)) == (15, 11, 5)
    assert hm(reset_hint("You've hit your limit · resets 3pm", base)) == (15, 15, 0)
    assert hm(reset_hint("resets at 15:30", base)) == (15, 15, 30)
    assert hm(reset_hint("try again at 8:15 a.m.", base)) == (16, 8, 15)  # passed -> tomorrow
    assert hm(reset_hint("resets 12am", base)) == (16, 0, 0)
    assert reset_hint("look at 5 files", base) is None
    assert reset_hint("at 25:00", base) is None
