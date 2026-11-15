# cli-relay

[![CI](https://github.com/mustikon/cli-relay/actions/workflows/ci.yml/badge.svg)](https://github.com/mustikon/cli-relay/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/cli-relay.svg)](https://pypi.org/project/cli-relay/)
[![Python](https://img.shields.io/pypi/pyversions/cli-relay.svg)](https://pypi.org/project/cli-relay/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**One worker interface over several coding-agent CLIs, with automatic fallback when a
subscription hits its limit.**

[Русская версия](README.ru.md)

You pay for Claude, ChatGPT and Google subscriptions, and each comes with its own agent CLI
and its own usage window. `cli-relay` runs a task on the first provider that can take it.
If that provider runs out of quota mid-task, the relay hands the work to the next one with
a briefing on what was already done.

```
cli-relay run "fix the failing test in tests/test_api.py" --cwd ~/code/app --permission edit

[cli-relay] claude started (claude-opus-5-5)
...
[cli-relay] claude failed [rate_limit]: You've hit your limit · resets 3pm
[cli-relay] switching claude -> antigravity (rate_limit)
[cli-relay] antigravity started
...
[cli-relay] done by antigravity
```

## Why

* **The official CLIs, unmodified.** `cli-relay` launches `claude`, `agy` and `codex`
  exactly as you would, under your own login. It never extracts, proxies or reuses OAuth
  tokens. You keep each tool's own permissions, hooks, `CLAUDE.md` / `AGENTS.md`, skills
  and MCP servers.
* **Limit-aware.** Claude Code reports its five-hour and weekly windows in its event
  stream. `cli-relay` records them and skips a provider *before* it fails when a window is
  nearly used up. A provider that failed on a limit is skipped until its reset time.
* **Handoff, not restart.** When it switches providers mid-task, the next agent gets the
  original task, the actions already taken and the last output. It is also told to check
  `git status` / `git diff` before continuing.
* **One event stream.** Every provider is normalised to the same events (`started`,
  `text_delta`, `tool_activity`, `quota_info`, `completed`, `failed`, plus the relay's
  `skipped` / `waiting` / `switched`). Use `--format ndjson` to drive it from another
  program, such as a voice assistant.
* **MCP server.** `cli-relay mcp` exposes `relay_run` and `relay_status`, so any MCP client
  can delegate work and get fallback for free.
* **Small and dependency-free.** The core uses only the Python standard library. That
  matters for a tool that runs next to all your credentials.

## Providers

| Type | CLI / endpoint | Status | Session resume | Quota telemetry |
|---|---|---|---|---|
| `claude` | Claude Code `claude -p --output-format stream-json` | verified live | `--resume` | five-hour / weekly windows |
| `antigravity` | Google Antigravity `agy -p --output-format stream-json` | verified live | `--conversation` | from error text |
| `codex` | OpenAI Codex `codex exec --json` | **experimental** (built from docs) | `exec resume` | from error text |
| `openai` | any OpenAI-compatible `/chat/completions` (OpenRouter, vLLM, Ollama, LM Studio…) | tested with a local server | — | HTTP 429 + `Retry-After` |

The `openai` type is a **text-only** fallback: it has no tools and cannot edit files.

## Install

```bash
pipx install cli-relay            # or: uv tool install cli-relay
pipx install "cli-relay[mcp]"     # with the MCP server
```

Requires Python 3.11+. Install and log in to the agent CLIs you want to use first.

## Usage

```bash
cli-relay status                                   # what is installed / limited
cli-relay run "explain src/auth.py"                # read-only by default
cli-relay run - < task.md --cwd ./repo --permission edit
cli-relay run "continue" --resume claude=SESSION_ID
cli-relay run "..." --providers antigravity,claude --format ndjson
cli-relay reset [provider]                         # forget recorded limits
```

### Permissions

`--permission` is mapped to each CLI's own permission system. Non-interactive runs never
wait for a prompt: anything that would ask is denied.

| `--permission` | Claude Code | Antigravity | Codex |
|---|---|---|---|
| `read_only` (default) | `--permission-mode dontAsk` | `--mode plan` | `--sandbox read-only` |
| `edit` | `--permission-mode acceptEdits` | `--mode accept-edits` | `--sandbox workspace-write` |
| `full` | `--permission-mode auto` | `--mode auto` | `workspace-write` + `--approve-for-me` |

A provider's `permission` setting is an upper bound, so a task never gets more than that.
`cli-relay` never uses "skip all permission checks" flags.

## Configuration

`cli-relay` looks for its config file in this order:

1. `--config PATH`
2. `$CLI_RELAY_CONFIG`
3. `./cli-relay.toml`
4. `%APPDATA%\cli-relay\config.toml` on Windows, `~/.config/cli-relay/config.toml` elsewhere

Without a file the chain is `claude → antigravity → codex`. See
[`examples/cli-relay.toml`](examples/cli-relay.toml):

```toml
order = ["claude", "antigravity", "codex", "openrouter", "local"]

[relay]
wait_if_reset_within_s = 900      # wait for a provider that resets within 15 min
skip_above_utilization = 0.97     # skip Claude when a usage window is ≥ 97 %
default_cooldown_s = 1800         # when a limit error carries no reset time
write_handoff_file = false        # also write HANDOFF.md into --cwd on a switch

[providers.claude]
model = "opus"
permission = "edit"               # upper bound for this provider

[providers.openrouter]
type = "openai"
base_url = "https://openrouter.ai/api/v1"
model = "vendor/model-id"          # any OpenRouter model id
api_key_env = "OPENROUTER_API_KEY"

[providers.local]
type = "openai"
base_url = "http://127.0.0.1:8000/v1"   # vLLM / Ollama / LM Studio
model = "your-served-model-name"
```

Recorded limits live in `%LOCALAPPDATA%\cli-relay\state.json` on Windows and
`~/.local/state/cli-relay/state.json` elsewhere. Override the path with `$CLI_RELAY_STATE`.

## As an MCP server

```bash
claude mcp add cli-relay -- cli-relay mcp
```

The server exposes two tools:
* `relay_run(prompt, cwd?, providers?, permission?)` returns `{ok, provider, text, session_id, switches, skipped}`.
* `relay_status()` returns installed providers and their known limits.

By default an MCP client can only request `read_only` work, whatever it asks for.
Otherwise a sandboxed agent could escalate by delegating to another one. To allow more,
set this in the config:

```toml
[mcp]
max_permission = "edit"
```

## As a library

```python
import asyncio
from cli_relay import Relay, Task, TextDelta, Completed
from cli_relay.config import load

async def main() -> None:
    cfg = load()
    relay = Relay(cfg.adapters(), cfg.quota(), cfg.policy)
    async for event in relay.run(Task(prompt="summarise README.md")):
        if isinstance(event, TextDelta):
            print(event.text, end="")
        elif isinstance(event, Completed):
            print(f"\n-- by {event.provider}")

asyncio.run(main())
```

## Terms of service

`cli-relay` is meant for **your own** subscriptions on **your own** machine. It runs each
vendor's official CLI as a normal user would, and the vendors' terms still apply. For
unattended automation, the vendors recommend API keys over subscription logins. Do not
use `cli-relay` to share one subscription between several people.

## Security notes

* Agent processes are started from an argument list, never through a shell.
* On Windows, `claude` and `codex` are npm `.cmd` shims, which `cmd.exe` re-parses. Prompts
  and system prompts are therefore always sent through stdin or a temp file. `cli-relay`
  refuses to put `cmd.exe` metacharacters on such a command line.
* Corrupted or interleaved NDJSON lines are salvaged rather than crashing a long run.

See [SECURITY.md](SECURITY.md) for how to report a vulnerability.

## Roadmap

- Voice and push approvals for permission prompts (Claude Code `PermissionRequest` hook,
  Codex app-server approvals)
- Codex app-server adapter with first-class rate-limit telemetry
- Git worktree per task with automatic WIP checkpoints
- Same-harness fallback (Claude Code against an Anthropic-compatible endpoint)

## License

[Apache-2.0](LICENSE)
