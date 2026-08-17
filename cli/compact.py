"""CLI: compact the agent message board (mirrors cli.snowball)."""

from __future__ import annotations

import sys

from scripts.compact import compact_command


def main(argv: list[str] | None = None) -> int:
    return compact_command(list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    sys.exit(main())
