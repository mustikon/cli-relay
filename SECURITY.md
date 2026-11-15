# Security policy

`cli-relay` launches coding agents that can read and change files on your machine, so
security reports are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private vulnerability reporting
("Security" tab → "Report a vulnerability") on this repository. Include steps to reproduce,
the affected version and your platform. You should get an answer within a week.

## Supported versions

Only the latest released minor version receives security fixes while the project is in
0.x.

## Design principles

* Agent CLIs are launched from an argument vector, never through a shell.
* User-supplied text (prompts, system prompts) is never placed on the command line of a
  Windows `.cmd`/`.bat` launcher; it goes through stdin or a temp file.
* The default permission level is `read_only`; "skip all permission checks" flags are
  never used.
* No OAuth tokens or credentials are read, stored or forwarded by `cli-relay`; each vendor
  CLI authenticates itself. API keys for HTTP providers are read from environment variables
  named in the config, never from the config file itself.
* The core package has no third-party runtime dependencies.
