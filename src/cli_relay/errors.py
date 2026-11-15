"""Heuristics that turn provider error text into a :class:`FailureKind`.

The CLIs do not document their quota errors consistently, so classification is
pattern based and deliberately conservative: anything not recognised is ``ERROR``.
"""

from __future__ import annotations

import re
import time

from cli_relay.events import FailureKind

_RATE_LIMIT = re.compile(
    r"rate[ _-]?limit|usage[ _-]?limit|limit (?:reached|exceeded)|quota|"
    r"resource[ _-]?exhausted|too many requests|\b429\b|out of (?:credits|usage)|"
    r"overloaded_error|\b529\b|hit your limit|limit will reset",
    re.IGNORECASE,
)
_AUTH = re.compile(
    r"\b401\b|\b403\b|unauthori[sz]ed|not (?:logged|signed) in|please (?:log|sign) ?in|"
    r"invalid (?:api[ _-]?key|token|credentials)|authentication|oauth token|login required",
    re.IGNORECASE,
)

_IN_DURATION = re.compile(
    r"\b(?:in|after)\s+(?:(\d+)\s*h(?:ours?|rs?)?\b)?\s*(?:(\d+)\s*m(?:in(?:ute)?s?)?\b)?",
    re.IGNORECASE,
)
_EPOCH = re.compile(r"\b(1[6-9]\d{8})\b")
# "try again at 11:05 AM", "resets 3pm", "resets at 15:30" (local wall-clock time)
_CLOCK = re.compile(
    r"\b(?:at|resets?(?:\s+at)?)\s+(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?(?![\w:])",
    re.IGNORECASE,
)


def classify(*texts: object) -> FailureKind:
    blob = " ".join(str(t) for t in texts if t)
    if _RATE_LIMIT.search(blob):
        return FailureKind.RATE_LIMIT
    if _AUTH.search(blob):
        return FailureKind.AUTH
    return FailureKind.ERROR


def reset_hint(text: str, now: float | None = None) -> float | None:
    """Best-effort reset time (epoch seconds) from messages like "try again in 2h 30m"."""
    now = time.time() if now is None else now
    for match in _IN_DURATION.finditer(text):
        hours, minutes = match.group(1), match.group(2)
        if hours or minutes:
            return now + int(hours or 0) * 3600 + int(minutes or 0) * 60
    epoch = _EPOCH.search(text)
    if epoch:
        value = float(epoch.group(1))
        if value > now:
            return value
    for match in _CLOCK.finditer(text):
        clock = _next_clock_time(match, now)
        if clock is not None:
            return clock
    return None


def _next_clock_time(match: re.Match[str], now: float) -> float | None:
    hour, minute, ampm = int(match.group(1)), match.group(2), match.group(3)
    if minute is None and ampm is None:
        return None  # a bare number ("at 5") is too ambiguous
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm.lower().startswith("p") else 0)
    elif hour > 23:
        return None
    mins = int(minute or 0)
    if mins > 59:
        return None
    local = time.localtime(now)
    target = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, hour, mins, 0, 0, 0, -1))
    if target <= now:  # already passed today -> tomorrow
        target = time.mktime(
            (local.tm_year, local.tm_mon, local.tm_mday + 1, hour, mins, 0, 0, 0, -1)
        )
    return target
