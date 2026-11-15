# Contributing

Thanks for helping! Bug reports with a captured event stream are especially valuable,
because the agent CLIs change their output often.

## Development setup

```bash
git clone https://github.com/mustikon/cli-relay && cd cli-relay
uv sync --all-extras
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy
```

The test suite never calls a real agent CLI. Subprocess tests replay NDJSON fixtures
through `tests/fakes/fake_cli.py`.

## Adding or fixing a provider

1. Capture a real stream, for example
   `claude -p "..." --output-format stream-json --verbose > out.ndjson`.
2. **Remove anything personal** (paths, user names, e-mails, tokens) and save the result
   under `tests/fixtures/`.
3. Implement `build()` and `parse()` in a `CliAdapter` subclass and register it in
   `cli_relay/adapters/__init__.py`.
4. Each run must end with exactly one `Completed` or `Failed` event. Classify quota
   errors as `FailureKind.RATE_LIMIT` and set `resets_at` whenever the provider says when
   the limit resets.

## Pull requests

* Keep the core free of third-party runtime dependencies.
* Add a `CHANGELOG.md` entry under *Unreleased*.
* CI must be green on Linux, macOS and Windows.
