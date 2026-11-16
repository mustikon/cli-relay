# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-11-16

### Added
- Images: `Task.images` / `cli-relay run --image FILE` (png, jpg, gif, webp; repeatable)
  and `images` on the MCP `relay_run` tool. Claude Code gets them as real attachments
  (one `--input-format stream-json` message with image blocks, so no file-read
  permission is needed), Codex through `--image`, Antigravity through `--add-dir` plus
  the paths in the prompt, OpenAI-compatible endpoints in the vision message format.
  Verified live on all three CLIs.
- Extra directories: `Task.add_dirs` / `--add-dir DIR`, passed as `--add-dir` to Claude
  Code, Codex and Antigravity.
- A missing or unsupported image, or a missing directory, fails at once with
  `Failed(provider="relay")` instead of being tried on every provider.

### Fixed
- `find_config`: an explicit config path that does not exist raises instead of falling
  back to another config file.
- Quota tracker: a rate-limit reset time already in the past falls back to the default
  cooldown.

## [0.1.1] - 2026-11-15

### Changed
- The MCP server supports the MCP Python SDK 2.x (`MCPServer`) as well as 1.x
  (`FastMCP`); the `mcp` extra now allows `mcp>=1.10,<3`.
- `relay_run` no longer sends per-provider log notifications (deprecated in SDK 2.x).

### Fixed
- `cli-relay mcp` reported "missing optional dependency" when an incompatible `mcp`
  version was installed; it now says which versions are supported.

## [0.1.0] - 2026-11-15

### Added
- `Relay`: runs a task on the first available provider and falls back on rate limits,
  auth errors, missing CLIs and generic errors (configurable).
- Adapters for Claude Code (`claude -p --output-format stream-json`), Google Antigravity
  (`agy -p --output-format stream-json`) and any OpenAI-compatible chat endpoint.
- Adapter for OpenAI Codex (`codex exec --json`); on Windows it also finds the `codex.exe`
  bundled with the Codex desktop app. Command layout and the rate-limit path are verified
  against codex-cli 0.167.
- Reset times are parsed from "in 2h 30m", wall-clock ("try again at 11:05 AM",
  "resets 3pm") and epoch formats.
- Normalised event stream: `started`, `text_delta`, `tool_activity`, `quota_info`,
  `completed`, `failed`, `skipped`, `waiting`, `switched`.
- Persistent quota tracker: remembers limits and reset times; skips a provider
  pre-emptively when a reported usage window is nearly exhausted.
- Mid-task handoff: the next provider receives the original task, actions taken and the
  last output, plus an instruction to inspect the working tree first.
- Permission levels (`read_only`, `edit`, `full`) mapped to each CLI's own flags, with a
  per-provider upper bound.
- `cli-relay` command (`run`, `status`, `reset`, `mcp`) with text and NDJSON output.
- MCP server (`cli-relay[mcp]`) with `relay_run` and `relay_status` tools. Permissions
  requested over MCP are capped by `[mcp] max_permission` (default `read_only`).
- TOML configuration.

[Unreleased]: https://github.com/mustikon/cli-relay/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/mustikon/cli-relay/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/mustikon/cli-relay/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/mustikon/cli-relay/releases/tag/v0.1.0
