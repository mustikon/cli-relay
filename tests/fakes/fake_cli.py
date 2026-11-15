"""Stand-in for an agent CLI in tests.

Usage: python fake_cli.py FIXTURE [--sleep SECONDS] [--exit CODE] [--stderr TEXT] [ignored...]

Prints FIXTURE line by line. If $FAKE_CLI_RECORD is set, writes {"argv": ..., "stdin": ...}
to that path first so tests can check what the adapter passed.
"""

import json
import os
import sys
import time


def main() -> int:
    args = sys.argv[1:]
    fixture = args[0]
    sleep = float(args[args.index("--sleep") + 1]) if "--sleep" in args else 0.0
    code = int(args[args.index("--exit") + 1]) if "--exit" in args else 0
    if sys.stdin is None or sys.stdin.isatty():
        stdin = ""
    else:
        stdin = sys.stdin.buffer.read().decode("utf-8")
    if record := os.environ.get("FAKE_CLI_RECORD"):
        with open(record, "w", encoding="utf-8") as fh:
            json.dump({"argv": sys.argv, "stdin": stdin}, fh)
    if "--stderr" in args:
        sys.stderr.write(args[args.index("--stderr") + 1])
        sys.stderr.flush()
    if fixture != "-":
        with open(fixture, encoding="utf-8") as fh:
            for line in fh:
                sys.stdout.write(line)
                sys.stdout.flush()
    if sleep:
        time.sleep(sleep)
    return code


if __name__ == "__main__":
    sys.exit(main())
