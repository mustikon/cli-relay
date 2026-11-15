from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cli_relay.adapters import AntigravityAdapter, ClaudeAdapter, CodexAdapter
from cli_relay.events import Completed, Failed, FailureKind
from cli_relay.models import Permission, ProviderSettings, Task
from cli_relay.proc import UnsafeArgumentError, check_cmd_shim_args

from .conftest import as_types, fake_command


def settings(name: str, **kw: object) -> ProviderSettings:
    return ProviderSettings(name=name, type=name, **kw)  # type: ignore[arg-type]


# -- command building ------------------------------------------------------------------


def test_claude_prompt_goes_to_stdin_never_argv() -> None:
    adapter = ClaudeAdapter(settings("claude", model="opus", extra_args=["--effort", "high"]))
    task = Task(
        prompt="rm -rf / & echo pwned",
        permission=Permission.EDIT,
        resume={"claude": "abc", "codex": "zzz"},
        system_prompt="Be brief.",
    )
    inv = adapter.build(task, ["claude"])
    try:
        assert inv.stdin == task.prompt.encode()
        assert all(task.prompt not in a for a in inv.argv)
        joined = " ".join(inv.argv)
        assert "--permission-mode acceptEdits" in joined
        assert "--permission-prompts none" in joined
        assert "--model opus" in joined and "--resume abc" in joined and "zzz" not in joined
        assert inv.argv[-2:] == ["--effort", "high"]
        sys_file = inv.argv[inv.argv.index("--append-system-prompt-file") + 1]
        assert Path(sys_file).read_text(encoding="utf-8") == "Be brief."
    finally:
        for path in inv.temp_files:
            Path(path).unlink()


@pytest.mark.parametrize(
    ("task_perm", "cap", "mode"),
    [
        (Permission.READ_ONLY, None, "dontAsk"),
        (Permission.FULL, None, "auto"),
        (Permission.FULL, Permission.EDIT, "acceptEdits"),
        (Permission.EDIT, Permission.READ_ONLY, "dontAsk"),
    ],
)
def test_permission_is_capped_by_provider(
    task_perm: Permission, cap: Permission | None, mode: str
) -> None:
    adapter = ClaudeAdapter(settings("claude", permission=cap))
    inv = adapter.build(Task(prompt="x", permission=task_perm), ["claude"])
    assert inv.argv[inv.argv.index("--permission-mode") + 1] == mode


def test_antigravity_long_prompt_goes_to_file() -> None:
    adapter = AntigravityAdapter(settings("antigravity"))
    inv = adapter.build(Task(prompt="y" * 50_000), ["agy"])
    try:
        prompt = inv.argv[inv.argv.index("-p") + 1]
        assert len(prompt) < 500
        assert Path(inv.temp_files[0]).read_text(encoding="utf-8") == "y" * 50_000
        assert "--add-dir" in inv.argv
        assert inv.argv[inv.argv.index("--mode") + 1] == "plan"
    finally:
        for path in inv.temp_files:
            Path(path).unlink()


def test_codex_reads_stdin_and_resumes() -> None:
    adapter = CodexAdapter(settings("codex"))
    inv = adapter.build(
        Task(prompt="hi", resume={"codex": "T1"}, permission=Permission.FULL), ["codex"]
    )
    # verified against codex-cli 0.167: exec options go before `resume`, which rejects them
    assert inv.argv == [
        "codex", "exec", "--sandbox", "workspace-write", "--approve-for-me",
        "resume", "T1", "--json", "--skip-git-repo-check", "-",
    ]  # fmt: skip
    assert inv.stdin == b"hi"
    plain = adapter.build(Task(prompt="hi"), ["codex"]).argv
    assert plain == [
        "codex", "exec", "--sandbox", "read-only", "--json", "--skip-git-repo-check", "-",
    ]  # fmt: skip


def test_codex_desktop_bundle_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import time as _time

    from cli_relay.adapters import codex as codex_mod

    old = tmp_path / "OpenAI" / "Codex" / "bin" / "aaa" / "codex.exe"
    new = tmp_path / "OpenAI" / "Codex" / "bin" / "bbb" / "codex.exe"
    for path in (old, new):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"")
    past = _time.time() - 3600
    os.utime(old, (past, past))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("cli_relay.adapters.base.resolve", lambda command: None)  # not on PATH
    assert codex_mod.desktop_bundled_codex() == new
    assert CodexAdapter(settings("codex")).command() == [str(new)]
    # an explicit command is never second-guessed
    explicit = CodexAdapter(settings("codex", command=["my-codex"]))
    assert explicit.command() is None


def test_cmd_shim_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    check_cmd_shim_args(["C:/npm/claude.cmd", "-p", "--model", "opus"])
    with pytest.raises(UnsafeArgumentError):
        check_cmd_shim_args(["C:/npm/claude.cmd", "--model", "x & calc"])
    check_cmd_shim_args(["C:/bin/agy.exe", "-p", "x & y"])  # real executables are fine


# -- real subprocess round trips -----------------------------------------------------


async def test_subprocess_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = tmp_path / "rec.json"
    monkeypatch.setenv("FAKE_CLI_RECORD", str(record))
    adapter = ClaudeAdapter(settings("claude", command=fake_command("claude_success.ndjson")))
    events = [e async for e in adapter.run(Task(prompt="привет, мир", cwd=tmp_path))]
    assert as_types(events)[0] == "started"
    assert isinstance(events[-1], Completed)
    seen = json.loads(record.read_text(encoding="utf-8"))
    assert seen["stdin"] == "привет, мир"


async def test_large_prompt_through_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = tmp_path / "rec.json"
    monkeypatch.setenv("FAKE_CLI_RECORD", str(record))
    adapter = ClaudeAdapter(settings("claude", command=fake_command("claude_success.ndjson")))
    big = "строка задачи\n" * 150_000  # ~2.4 MB, far above any pipe buffer
    events = [e async for e in adapter.run(Task(prompt=big))]
    assert isinstance(events[-1], Completed)
    assert json.loads(record.read_text(encoding="utf-8"))["stdin"] == big


async def test_subprocess_timeout() -> None:
    adapter = ClaudeAdapter(settings("claude", command=fake_command("-", "--sleep", "30")))
    events = [e async for e in adapter.run(Task(prompt="x", timeout_s=1))]
    assert isinstance(events[-1], Failed) and events[-1].kind is FailureKind.TIMEOUT


async def test_subprocess_without_result_reports_stderr() -> None:
    cmd = fake_command("-", "--exit", "3", "--stderr", "Error: not logged in")
    adapter = ClaudeAdapter(settings("claude", command=cmd))
    events = [e async for e in adapter.run(Task(prompt="x"))]
    failed = events[-1]
    assert isinstance(failed, Failed) and failed.kind is FailureKind.AUTH
    assert "not logged in" in failed.message


async def test_not_installed() -> None:
    adapter = ClaudeAdapter(settings("claude", command=["definitely-not-a-real-cli-xyz"]))
    assert adapter.available()[0] is False
    events = [e async for e in adapter.run(Task(prompt="x"))]
    assert isinstance(events[-1], Failed) and events[-1].kind is FailureKind.NOT_INSTALLED
