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
    #: Image files to show the agent with the prompt (screenshots, photos). Claude and
    #: Codex get them as real attachments; Antigravity gets their folder and paths.
    images: tuple[Path, ...] = ()
    #: Extra directories the agent may read (and, with ``edit``/``full``, write) besides
    #: ``cwd``.
    add_dirs: tuple[Path, ...] = ()

    def with_prompt(self, prompt: str) -> Task:
        return replace(self, prompt=prompt)

    def check_files(self) -> str | None:
        """An error message if an image or extra directory is unusable, else None.

        Images are checked by content, not only by name: a renamed or symlinked text
        file (a key, a config) is refused, so ``images`` cannot be used to send
        arbitrary files to a provider.
        """
        for image in self.images:
            if not image.is_file():
                return f"image not found: {image}"
            if image.suffix.lower() not in IMAGE_TYPES:
                return f"not a supported image type ({', '.join(IMAGE_TYPES)}): {image}"
            try:
                size = image.stat().st_size
                with image.open("rb") as fh:
                    head = fh.read(16)
            except OSError as exc:
                return f"cannot read image {image}: {exc}"
            if size > MAX_IMAGE_BYTES:
                return f"image larger than {MAX_IMAGE_BYTES // 2**20} MB: {image}"
            if not _looks_like_image(head):
                return f"not an image file (content does not match): {image}"
        for folder in self.add_dirs:
            if not folder.is_dir():
                return f"directory not found: {folder}"
        return None


#: Providers reject much larger images anyway; this also bounds what is read into memory.
MAX_IMAGE_BYTES = 20 * 2**20


def _looks_like_image(head: bytes) -> bool:
    return (
        head.startswith(b"\x89PNG\r\n\x1a\n")
        or head.startswith(b"\xff\xd8\xff")
        or head[:6] in (b"GIF87a", b"GIF89a")
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    )


#: Image suffix -> media type, for the formats every supported CLI accepts.
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


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
