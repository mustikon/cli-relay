from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from cli_relay.adapters import OpenAICompatAdapter
from cli_relay.events import Completed, Failed, FailureKind, TextDelta
from cli_relay.models import ProviderSettings, Task


class Handler(BaseHTTPRequestHandler):
    mode = "ok"
    seen: list[dict[str, object]] = []

    def log_message(self, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.seen.append({"body": body, "auth": self.headers.get("Authorization")})
        if Handler.mode == "limit":
            self.send_response(429)
            self.send_header("Retry-After", "120")
            self.end_headers()
            self.wfile.write(b'{"error":"rate limited"}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for piece in ("При", "вет"):
            chunk = {"choices": [{"delta": {"content": piece}}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b'data: {"choices":[],"usage":{"total_tokens":7}}\n\ndata: [DONE]\n\n')


@pytest.fixture
def server() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    Handler.seen = []
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


def adapter(url: str, key_env: str | None = None) -> OpenAICompatAdapter:
    return OpenAICompatAdapter(
        ProviderSettings(
            name="local", type="openai", base_url=url, model="qwen", api_key_env=key_env
        )
    )


async def test_streams_text(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    Handler.mode = "ok"
    monkeypatch.setenv("TEST_LOCAL_KEY", "sk-test")
    events = [
        e
        async for e in adapter(server, "TEST_LOCAL_KEY").run(
            Task(prompt="hi", system_prompt="be nice")
        )
    ]
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Привет"
    done = events[-1]
    assert isinstance(done, Completed) and done.text == "Привет"
    assert done.usage == {"total_tokens": 7}
    seen = Handler.seen[0]
    assert seen["auth"] == "Bearer sk-test"
    body = seen["body"]
    assert isinstance(body, dict) and body["messages"][0] == {
        "role": "system",
        "content": "be nice",
    }


async def test_images_use_the_vision_format(server: str, tmp_path: Path) -> None:
    Handler.mode = "ok"
    shot = tmp_path / "screen.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n")
    events = [e async for e in adapter(server).run(Task(prompt="что это?", images=(shot,)))]
    assert isinstance(events[-1], Completed)
    body = Handler.seen[0]["body"]
    assert isinstance(body, dict)
    content = body["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": "что это?"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_rate_limit(server: str) -> None:
    Handler.mode = "limit"
    events = [e async for e in adapter(server).run(Task(prompt="hi"))]
    failed = events[-1]
    assert isinstance(failed, Failed) and failed.kind is FailureKind.RATE_LIMIT
    assert failed.resets_at is not None


def test_unconfigured() -> None:
    bare = OpenAICompatAdapter(ProviderSettings(name="x", type="openai"))
    assert bare.available() == (False, "no base_url configured")
