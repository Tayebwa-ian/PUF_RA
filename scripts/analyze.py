"""Standalone runner for ``puf analyze`` (mirrors the ``puf analyze`` CLI in cli/analyze.py).

    python -m scripts.analyze all --db results.db --out-dir data/analysis
    python -m scripts.analyze relevance --mode direct
"""

from __future__ import annotations

import sys

from cli.analyze import main


if __name__ == "__main__":
    sys.exit(main())
