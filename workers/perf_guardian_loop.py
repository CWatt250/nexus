#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Performance Guardian tick loop (Phase 16.7)."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from safety import perf_guardian  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    perf_guardian.run_forever(poll_seconds=60.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
