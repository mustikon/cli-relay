"""Provider adapters and the type registry used by the config loader."""

from __future__ import annotations

from cli_relay.adapters.antigravity import AntigravityAdapter
from cli_relay.adapters.base import Adapter, CliAdapter
from cli_relay.adapters.claude import ClaudeAdapter
from cli_relay.adapters.codex import CodexAdapter
from cli_relay.adapters.openai_compat import OpenAICompatAdapter
from cli_relay.models import ProviderSettings

REGISTRY: dict[str, type[Adapter]] = {
    cls.type_name: cls
    for cls in (ClaudeAdapter, AntigravityAdapter, CodexAdapter, OpenAICompatAdapter)
}


def create(settings: ProviderSettings) -> Adapter:
    try:
        cls = REGISTRY[settings.type]
    except KeyError:
        known = ", ".join(sorted(REGISTRY))
        raise ValueError(
            f"provider {settings.name!r}: unknown type {settings.type!r} (known: {known})"
        ) from None
    return cls(settings)


__all__ = [
    "REGISTRY",
    "Adapter",
    "AntigravityAdapter",
    "ClaudeAdapter",
    "CliAdapter",
    "CodexAdapter",
    "OpenAICompatAdapter",
    "create",
]
