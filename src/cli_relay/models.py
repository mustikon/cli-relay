"""Task and provider configuration models."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from pathlib import Path


class Permission(enum.StrEnum):
    """What a worker may do, mapped to each CLI's own permission flags.

    * ``read_only`` - read files and answer; anything that would write or run commands
      is denied without prompting.
    * ``edit`` - may edit files inside the working directory; no shell commands unless
      the provider config allows them explicitly.
    * ``full`` - the provider's own automatic mode (Claude ``auto``, Antigravity ``auto``,
      Codex ``workspace-write`` + ``--approve-for-me``). Never a "skip all checks" mode.
    """

    READ_ONLY = "read_only"
    EDIT = "edit"
    FULL = "full"

    @property
    def rank(self) -> int:
        return ("read_only", "edit", "full").index(self.value)

    def capped_by(self, cap: Permission | None) -> Permission:
        if cap is None or self.rank <= cap.rank:
            return self
        return cap


@dataclass(frozen=True)
class Task:
    prompt: str
    cwd: Path | None = None
    permission: Permission = Permission.READ_ONLY
    #: Extra instructions appended to the provider's system prompt.
    system_prompt: str | None = None
    #: Session id to resume, per provider name.
    resume: dict[str, str] = field(default_factory=dict)
    #: Hard wall-clock limit for one provider attempt, in seconds.
    timeout_s: float | None = None

    def with_prompt(self, prompt: str) -> Task:
        return replace(self, prompt=prompt)


@dataclass(frozen=True)
class ProviderSettings:
    """Configuration of one provider entry (``[providers.<name>]`` in the config file)."""

    name: str
    type: str
    #: Full launcher command, e.g. ``["claude"]`` or ``["wsl", "-e", "claude"]``.
    #: ``None`` means the adapter's default binary looked up on ``PATH``.
    command: list[str] | None = None
    model: str | None = None
    #: Upper bound: a task never runs with more than this on this provider.
    permission: Permission | None = None
    extra_args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # OpenAI-compatible HTTP providers only:
    base_url: str | None = None
    api_key_env: str | None = None
