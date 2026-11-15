"""Subprocess plumbing: launch a CLI, stream stdout lines, stop it cleanly.

Security notes
--------------
* Commands are always launched as an argument vector, never through a shell.
* On Windows, ``.cmd``/``.bat`` launchers (npm shims such as ``claude.cmd``) are run by
  ``cmd.exe``, which re-parses the arguments. Adapters therefore pass user text through
  stdin or temp files, and :func:`check_cmd_shim_args` refuses arguments containing
  ``cmd.exe`` metacharacters for such launchers.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import subprocess
import sys
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

_STREAM_LIMIT = 16 * 1024 * 1024
_STDERR_TAIL = 64 * 1024
_CMD_META = set('&|<>^%!"\r\n')


class UnsafeArgumentError(ValueError):
    pass


def resolve(command: Sequence[str]) -> list[str] | None:
    """Resolve ``command[0]`` on ``PATH``; ``None`` if it is not installed."""
    if not command:
        return None
    exe = shutil.which(command[0])
    if exe is None:
        return None
    return [exe, *command[1:]]


def is_cmd_shim(exe: str) -> bool:
    return sys.platform == "win32" and exe.lower().endswith((".cmd", ".bat"))


def check_cmd_shim_args(argv: Sequence[str]) -> None:
    if not argv or not is_cmd_shim(argv[0]):
        return
    for arg in argv[1:]:
        bad = _CMD_META.intersection(arg)
        if bad:
            raise UnsafeArgumentError(
                f"refusing to pass {arg!r} to {Path(argv[0]).name}: cmd.exe would interpret "
                f"{''.join(sorted(bad))!r}. Pass it via stdin/a file, or point the provider "
                f"'command' at the real executable instead of the .cmd shim."
            )


class ProcessRun:
    """One running CLI process. Iterate :meth:`lines`; afterwards read ``returncode``."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        stdin_data: bytes | None = None,
        env: dict[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> None:
        check_cmd_shim_args(argv)
        self.argv = list(argv)
        self.cwd = cwd
        self.stdin_data = stdin_data
        self.env = {**os.environ, **env} if env else None
        self.timeout_s = timeout_s
        self.returncode: int | None = None
        self.timed_out = False
        self._stderr = bytearray()
        self._proc: asyncio.subprocess.Process | None = None

    @property
    def stderr_tail(self) -> str:
        return self._stderr.decode("utf-8", errors="replace")

    async def lines(self) -> AsyncIterator[str]:
        kwargs: dict[str, object] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        proc = await asyncio.create_subprocess_exec(
            *self.argv,
            cwd=str(self.cwd) if self.cwd else None,
            env=self.env,
            stdin=asyncio.subprocess.PIPE if self.stdin_data is not None else subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_STREAM_LIMIT,
            **kwargs,  # type: ignore[arg-type]
        )
        self._proc = proc
        stderr_task = asyncio.create_task(self._drain_stderr(proc))
        # Feed stdin concurrently with reading stdout, so a large prompt can never deadlock
        # against a child that is already writing output.
        stdin_task = asyncio.create_task(self._feed_stdin(proc))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_s if self.timeout_s else None
        assert proc.stdout is not None
        try:
            while True:
                remaining = None if deadline is None else max(0.0, deadline - loop.time())
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(), remaining)
                except TimeoutError:
                    self.timed_out = True
                    await self.stop()
                    break
                except ValueError:
                    # A single line longer than the stream limit: asyncio drops it from the
                    # buffer; skip it rather than abort the whole run.
                    continue
                if not raw:
                    break
                yield raw.decode("utf-8", errors="replace")
            self.returncode = await proc.wait()
        except BaseException:
            await self.stop()
            raise
        finally:
            if not stdin_task.done():
                stdin_task.cancel()
            await asyncio.gather(stdin_task, stderr_task, return_exceptions=True)

    async def _feed_stdin(self, proc: asyncio.subprocess.Process) -> None:
        if self.stdin_data is None or proc.stdin is None:
            return
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            proc.stdin.write(self.stdin_data)
            await proc.stdin.drain()
            proc.stdin.close()

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        while chunk := await proc.stderr.read(8192):
            self._stderr.extend(chunk)
            if len(self._stderr) > _STDERR_TAIL:
                del self._stderr[: len(self._stderr) - _STDERR_TAIL]

    async def stop(self, grace_s: float = 5.0) -> None:
        """Interrupt politely (SIGINT / CTRL_BREAK), then kill the whole process tree."""
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, OSError):
            if sys.platform == "win32":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(proc.pid, signal.SIGINT)
        try:
            await asyncio.wait_for(proc.wait(), grace_s)
        except TimeoutError:
            await _kill_tree(proc)
        self.returncode = proc.returncode


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError, OSError):
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/T",
                "/F",
                "/PID",
                str(proc.pid),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await killer.wait()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), 5)
