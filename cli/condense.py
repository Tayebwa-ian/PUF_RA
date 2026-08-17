"""CLI: condense the agent message board (mirrors cli.snowball)."""

from __future__ import annotations

import sys

from scripts.condense import condense_command


def main(argv: list[str] | None = None) -> int:
    return condense_command(list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    sys.exit(main())
