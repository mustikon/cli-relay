from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from cli_relay import config as config_mod
from cli_relay.cli import main
from cli_relay.events import Failed, FailureKind
from cli_relay.models import Permission
from cli_relay.quota import QuotaTracker

from .conftest import fake_command


def write_config(tmp_path: Path, claude_fixture: str, agy_fixture: str) -> Path:
    def toml_list(items: list[str]) -> str:
        return "[" + ", ".join(json.dumps(i) for i in items) + "]"

    path = tmp_path / "cli-relay.toml"
    path.write_text(
        f"""
order = ["claude", "antigravity"]

[relay]
state_path = {json.dumps(str(tmp_path / "state.json"))}
default_cooldown_s = 600

[providers.claude]
command = {toml_list(fake_command(claude_fixture))}
permission = "edit"

[providers.antigravity]
command = {toml_list(fake_command(agy_fixture))}

[providers.openrouter]
type = "openai"
base_url = "https://openrouter.ai/api/v1"
model = "some/model"
api_key_env = "CLI_RELAY_TEST_KEY_THAT_IS_NOT_SET"
""",
        encoding="utf-8",
    )
    return path


def test_parse_defaults() -> None:
    cfg = config_mod.parse({})
    assert cfg.order == ["claude", "antigravity", "codex"]
    assert set(cfg.providers) == {"claude", "antigravity", "codex"}


def test_parse_file(tmp_path: Path) -> None:
    cfg = config_mod.load(write_config(tmp_path, "claude_success.ndjson", "agy_success.ndjson"))
    assert cfg.order == ["claude", "antigravity"]
    assert cfg.providers["claude"].permission is Permission.EDIT
    assert cfg.providers["openrouter"].type == "openai"
    assert cfg.default_cooldown_s == 600
    adapters = cfg.adapters(["openrouter"])
    assert adapters[0].available() == (
        False,
        "environment variable CLI_RELAY_TEST_KEY_THAT_IS_NOT_SET is not set",
    )
    with pytest.raises(ValueError, match="unknown provider"):
        cfg.adapters(["nope"])
    with pytest.raises(ValueError, match="unknown type"):
        config_mod.parse({"providers": {"x": {"type": "nope"}}}).adapters(["x"])


def test_missing_explicit_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fallback = tmp_path / "user-config.toml"
    fallback.write_text('order = ["claude"]\n', encoding="utf-8")
    monkeypatch.setattr(config_mod, "user_config_path", lambda: fallback)
    with pytest.raises(FileNotFoundError):
        config_mod.load(tmp_path / "missing.toml")


def test_cli_run_falls_back(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = write_config(tmp_path, "claude_rate_limited.ndjson", "agy_success.ndjson")
    code = main(["--config", str(cfg), "run", "do it", "--format", "ndjson"])
    assert code == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    types = [e["type"] for e in events]
    assert "switched" in types
    assert events[-1]["type"] == "completed" and events[-1]["provider"] == "antigravity"
    failed = next(e for e in events if e["type"] == "failed")
    assert failed["kind"] == "rate_limit"

    # second run: claude is remembered as limited and skipped
    code = main(["--config", str(cfg), "status", "--format", "json"])
    status = json.loads(capsys.readouterr().out)
    claude = next(p for p in status["providers"] if p["provider"] == "claude")
    assert claude["blocked_until"] is not None


def test_cli_text_output_and_reset(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = write_config(tmp_path, "claude_success.ndjson", "agy_success.ndjson")
    QuotaTracker(tmp_path / "state.json").observe(
        Failed(
            provider="antigravity",
            kind=FailureKind.RATE_LIMIT,
            message="x",
            resets_at=time.time() + 999,
        )
    )
    assert main(["--config", str(cfg), "run", "q"]) == 0
    out = capsys.readouterr()
    assert out.out.strip() == "The secret word is banana."
    assert "done by claude" in out.err
    assert main(["--config", str(cfg), "status"]) == 0
    assert "limited" in capsys.readouterr().out
    assert main(["--config", str(cfg), "reset"]) == 0
    assert main(["--config", str(cfg), "status"]) == 0
    assert "limited" not in capsys.readouterr().out


def test_cli_prompt_from_stdin_is_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import io
    import sys

    record = tmp_path / "rec.json"
    monkeypatch.setenv("FAKE_CLI_RECORD", str(record))
    raw = io.BytesIO("﻿Почини тест — срочно".encode())
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(raw, encoding="cp1252"))
    cfg = write_config(tmp_path, "claude_success.ndjson", "agy_success.ndjson")
    assert main(["--config", str(cfg), "run", "-"]) == 0
    assert json.loads(record.read_text(encoding="utf-8"))["stdin"] == "Почини тест — срочно"


def test_cli_bad_resume(tmp_path: Path) -> None:
    cfg = write_config(tmp_path, "claude_success.ndjson", "agy_success.ndjson")
    assert main(["--config", str(cfg), "run", "x", "--resume", "oops"]) == 2


def test_cli_all_fail_exit_code(tmp_path: Path) -> None:
    cfg = write_config(tmp_path, "claude_rate_limited.ndjson", "agy_quota.ndjson")
    assert main(["--config", str(cfg), "run", "x"]) == 1
