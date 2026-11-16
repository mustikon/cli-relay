"""Any OpenAI-compatible chat endpoint: OpenRouter, a paid API, vLLM, Ollama, LM Studio...

This is a *text-only* fallback: it has no tools and cannot edit files. It is meant as the
last links of the chain ("answer from somewhere rather than nowhere"). Standard library
only - no HTTP client dependency.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from typing import Any, ClassVar

from cli_relay.adapters.base import Adapter
from cli_relay.errors import classify, reset_hint
from cli_relay.events import Completed, Event, Failed, FailureKind, Started, TextDelta
from cli_relay.models import IMAGE_TYPES, Task

_DONE = object()


class OpenAICompatAdapter(Adapter):
    type_name: ClassVar[str] = "openai"

    def available(self) -> tuple[bool, str]:
        if not self.settings.base_url:
            return False, "no base_url configured"
        if not self.settings.model:
            return False, "no model configured"
        env = self.settings.api_key_env
        if env and not os.environ.get(env):
            return False, f"environment variable {env} is not set"
        return True, ""

    async def run(self, task: Task) -> AsyncIterator[Event]:
        ok, reason = self.available()
        if not ok:
            yield Failed(provider=self.name, kind=FailureKind.NOT_INSTALLED, message=reason)
            return
        if problem := task.check_files():
            yield Failed(provider=self.name, kind=FailureKind.ERROR, message=problem)
            return
        messages: list[dict[str, Any]] = []
        if task.system_prompt:
            messages.append({"role": "system", "content": task.system_prompt})
        if task.images:
            # OpenAI vision format; endpoints whose model has no vision answer with an
            # error, which the relay treats like any other failure.
            content: list[dict[str, Any]] = [{"type": "text", "text": task.prompt}]
            for image in task.images:
                try:
                    raw = image.read_bytes()
                except OSError as exc:
                    yield Failed(provider=self.name, kind=FailureKind.ERROR, message=str(exc))
                    return
                data = base64.b64encode(raw).decode("ascii")
                media = IMAGE_TYPES[image.suffix.lower()]
                data_url = f"data:{media};base64,{data}"
                content.append({"type": "image_url", "image_url": {"url": data_url}})
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": task.prompt})
        body = json.dumps(
            {"model": self.settings.model, "messages": messages, "stream": True}
        ).encode()
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.settings.api_key_env:
            headers["Authorization"] = f"Bearer {os.environ[self.settings.api_key_env]}"
        url = self.settings.base_url.rstrip("/") + "/chat/completions"  # type: ignore[union-attr]
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[object] = asyncio.Queue()
        stop = threading.Event()

        def put(item: object) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, item)

        def worker() -> None:
            try:
                with urllib.request.urlopen(req, timeout=task.timeout_s or 600) as resp:  # noqa: S310
                    for raw in resp:
                        if stop.is_set():
                            return
                        put(raw)
            except urllib.error.HTTPError as exc:
                put(exc)
            except Exception as exc:
                put(exc)
            finally:
                put(_DONE)

        started = time.monotonic()
        thread = threading.Thread(target=worker, name=f"cli-relay-{self.name}", daemon=True)
        thread.start()
        yield Started(provider=self.name, model=self.settings.model)
        parts: list[str] = []
        usage: dict[str, Any] = {}
        try:
            while True:
                item = await queue.get()
                if item is _DONE:
                    break
                if isinstance(item, urllib.error.HTTPError):
                    yield self._http_failure(item)
                    return
                if isinstance(item, Exception):
                    yield Failed(provider=self.name, kind=classify(item), message=str(item))
                    return
                assert isinstance(item, bytes)
                line = item.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    continue
                with contextlib.suppress(ValueError):
                    chunk = json.loads(payload)
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    for choice in chunk.get("choices") or []:
                        text = (choice.get("delta") or {}).get("content")
                        if text:
                            parts.append(text)
                            yield TextDelta(provider=self.name, text=text)
            yield Completed(
                provider=self.name,
                text="".join(parts),
                usage=usage,
                duration_s=time.monotonic() - started,
            )
        finally:
            stop.set()

    def _http_failure(self, exc: urllib.error.HTTPError) -> Failed:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
        except Exception:
            detail = ""
        message = f"HTTP {exc.code}: {detail or exc.reason}"
        resets_at = None
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        if retry_after and retry_after.strip().isdigit():
            resets_at = time.time() + int(retry_after.strip())
        if exc.code == 429:
            kind = FailureKind.RATE_LIMIT
        elif exc.code in (401, 403):
            kind = FailureKind.AUTH
        else:
            kind = classify(message)
        return Failed(
            provider=self.name,
            kind=kind,
            message=message,
            resets_at=resets_at or reset_hint(message),
        )
