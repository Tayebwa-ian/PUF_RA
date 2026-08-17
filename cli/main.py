"""Top-level CLI dispatcher for the PUF Research Pipeline.

Routes the first argument to the appropriate subcommand module so all commands
are reachable from a single ``puf`` entry point:

    puf import ...      puf relevance ...    puf screen ...
    puf snowball ...    puf tui ...          puf eval ...

Each subcommand module exposes ``main(argv)`` and receives the remaining
arguments (without the subcommand name). The ``cli.import`` module name collides
with the ``import`` keyword, so modules are loaded dynamically.
"""

from __future__ import annotations

import importlib
import sys

_DISPATCH = {
    "import": "cli.import",
    "relevance": "cli.relevance",
    "screen": "cli.screen",
    "snowball": "cli.snowball",
    "tui": "cli.tui",
    "eval": "cli.eval",
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in _DISPATCH:
        print(
            "Usage: puf {import|relevance|screen|snowball|tui|eval} ...",
            file=sys.stderr,
        )
        return 2
    cmd, rest = argv[0], argv[1:]
    module = importlib.import_module(_DISPATCH[cmd])
    return module.main(rest)


if __name__ == "__main__":
    sys.exit(main())
