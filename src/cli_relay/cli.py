"""Command line interface: ``cli-relay run | status | reset | mcp``."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import TextIO

from cli_relay import __version__
from cli_relay.config import Config, load
from cli_relay.events import (
    Completed,
    Event,
    Failed,
    QuotaInfo,
    Skipped,
    Started,
    Switched,
    TextDelta,
    ToolActivity,
    Waiting,
)
from cli_relay.models import Permission, Task
from cli_relay.relay import Relay

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli-relay",
        description="Run a task on Claude Code / Antigravity / Codex / an API, "
        "falling back to the next one when a provider hits its limit.",
    )
    parser.add_argument("--version", action="version", version=f"cli-relay {__version__}")
    parser.add_argument("--config", help="path to cli-relay.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run a task")
    run.add_argument("prompt", help="task text, or '-' to read it from stdin")
    run.add_argument("--cwd", type=Path, help="working directory for the agent")
    run.add_argument("--providers", help="comma-separated provider order (overrides config)")
    run.add_argument(
        "--permission",
        choices=[p.value for p in Permission],
        default=Permission.READ_ONLY.value,
        help="what the agent may do (default: read_only)",
    )
    run.add_argument("--system", help="extra system instructions for the agent")
    run.add_argument(
        "--resume",
        action="append",
        default=[],
        metavar="PROVIDER=SESSION",
        help="resume a provider session (repeatable)",
    )
    run.add_argument("--timeout", type=float, help="seconds per provider attempt")
    run.add_argument(
        "--format",
        choices=["text", "ndjson"],
        default="text",
        help="text for humans, ndjson for programs (default: text)",
    )

    status = sub.add_parser("status", help="show providers and known limits")
    status.add_argument("--format", choices=["text", "json"], default="text")

    reset = sub.add_parser("reset", help="forget recorded limits")
    reset.add_argument("provider", nargs="?", help="only this provider")

    sub.add_parser(
        "mcp",
        help="serve cli-relay as an MCP server over stdio (needs: pip install 'cli-relay[mcp]')",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = _build_parser().parse_args(argv)
    try:
        config = load(args.config)
    except (OSError, ValueError) as exc:
        print(f"cli-relay: config error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        if args.command == "run":
            return asyncio.run(_run(args, config))
        if args.command == "status":
            return _status(args, config)
        if args.command == "reset":
            config.quota().clear(args.provider)
            print("limits cleared" + (f" for {args.provider}" if args.provider else ""))
            return EXIT_OK
        if args.command == "mcp":
            from cli_relay.mcp_server import serve

            serve(config)
            return EXIT_OK
    except KeyboardInterrupt:
        return 130
    except ValueError as exc:
        print(f"cli-relay: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_USAGE


async def _run(args: argparse.Namespace, config: Config) -> int:
    # Read stdin as UTF-8 regardless of the console code page (cp1252/cp866 on Windows).
    prompt = _read_stdin_utf8() if args.prompt == "-" else args.prompt
    if not prompt.strip():
        print("cli-relay: empty prompt", file=sys.stderr)
        return EXIT_USAGE
    resume: dict[str, str] = {}
    for item in args.resume:
        provider, sep, session = item.partition("=")
        if not sep or not session:
            raise ValueError(f"--resume expects PROVIDER=SESSION, got {item!r}")
        resume[provider] = session
    order = [p.strip() for p in args.providers.split(",")] if args.providers else None
    relay = Relay(config.adapters(order), config.quota(), config.policy)
    task = Task(
        prompt=prompt,
        cwd=args.cwd,
        permission=Permission(args.permission),
        system_prompt=args.system,
        resume=resume,
        timeout_s=args.timeout,
    )
    last: Event | None = None
    async for event in relay.run(task):
        last = event
        if args.format == "ndjson":
            print(json.dumps(event.to_dict(), ensure_ascii=False), flush=True)
        else:
            _print_human(event, sys.stdout, sys.stderr)
    return EXIT_OK if isinstance(last, Completed) else EXIT_FAILED


def _read_stdin_utf8() -> str:
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:  # replaced stdin (tests, embedding)
        return sys.stdin.read()
    return buffer.read().decode("utf-8-sig", errors="replace")  # type: ignore[no-any-return]


def _print_human(event: Event, out: TextIO, err: TextIO) -> None:
    def note(text: str) -> None:
        print(f"\n[cli-relay] {text}", file=err, flush=True)

    if isinstance(event, TextDelta):
        out.write(event.text)
        out.flush()
    elif isinstance(event, Started):
        note(f"{event.provider} started" + (f" ({event.model})" if event.model else ""))
    elif isinstance(event, ToolActivity):
        note(f"{event.provider}: {event.tool} {event.detail}".rstrip())
    elif isinstance(event, QuotaInfo):
        if event.utilization is not None and event.utilization >= 0.8:
            note(f"{event.provider} {event.window} usage at {event.utilization:.0%}")
    elif isinstance(event, Skipped):
        note(f"skipping {event.provider}: {event.reason}")
    elif isinstance(event, Waiting):
        until = time.strftime("%H:%M", time.localtime(event.until))
        note(f"waiting for {event.provider} until {until}")
    elif isinstance(event, Switched):
        note(f"switching {event.provider} -> {event.to_provider} ({event.reason})")
    elif isinstance(event, Failed):
        note(f"{event.provider} failed [{event.kind.value}]: {event.message}")
    elif isinstance(event, Completed):
        # On a subscription this is the API-equivalent price reported by the CLI, not a bill.
        cost = f", api-equivalent ${event.cost_usd:.4f}" if event.cost_usd else ""
        note(
            f"done by {event.provider}"
            + (f" (session {event.session_id}{cost})" if event.session_id else "")
        )


def _status(args: argparse.Namespace, config: Config) -> int:
    quota = config.quota()
    rows = []
    for adapter in config.adapters():
        ok, reason = adapter.available()
        block = quota.blocked(adapter.name)
        rows.append(
            {
                "provider": adapter.name,
                "type": adapter.settings.type,
                "available": ok,
                "reason": reason,
                "blocked_until": block.until if block else None,
                "blocked_reason": block.reason if block else None,
                "windows": quota.snapshot().get(adapter.name, {}).get("windows", {}),
            }
        )
    if args.format == "json":
        print(
            json.dumps(
                {
                    "config": str(config.source) if config.source else None,
                    "order": config.order,
                    "providers": rows,
                },
                indent=2,
            )
        )
        return EXIT_OK
    print(f"config: {config.source or '(built-in defaults)'}")
    for row in rows:
        state = (
            "ready"
            if row["available"] and not row["blocked_until"]
            else ("limited" if row["blocked_until"] else "unavailable")
        )
        line = f"  {row['provider']:<14} {state:<12}"
        if row["blocked_until"]:
            until = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["blocked_until"]))
            line += f" until {until} ({row['blocked_reason']})"
        elif not row["available"]:
            line += f" {row['reason']}"
        for name, window in row["windows"].items():
            if window.get("utilization") is not None:
                line += f"  {name}={window['utilization']:.0%}"
        print(line)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
