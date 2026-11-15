"""Tolerant NDJSON parsing.

CLI event streams are usually one JSON object per line, but in practice a line can be
corrupted (e.g. an event written into the middle of another one) or contain stray log
text. We never want one bad line to kill a long agent run, so we recover every object
we can and drop the rest.
"""

from __future__ import annotations

import json
from typing import Any

_decoder = json.JSONDecoder()


def parse_line(line: str) -> list[dict[str, Any]]:
    """Return every JSON object that can be recovered from ``line``."""
    line = line.strip()
    if not line:
        return []
    try:
        obj = json.loads(line)
    except ValueError:
        return _salvage(line)
    return [obj] if isinstance(obj, dict) else []


def _salvage(line: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    pos = line.find("{")
    while pos != -1:
        try:
            obj, end = _decoder.raw_decode(line, pos)
        except ValueError:
            pos = line.find("{", pos + 1)
            continue
        if isinstance(obj, dict):
            found.append(obj)
        pos = line.find("{", end)
    return found
