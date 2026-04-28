#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Standalone scheduler tick loop (Phase 16.5).

Runs `core.scheduler.run_forever()` so triggers fire at their UTC
deadlines and enqueue tasks into the Phase 15 queue. Heavy work runs
on the task_worker — never inside this loop.

Service: `nexus-scheduler.service` (Restart=always).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import scheduler  # noqa: E402


def main() -> int:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
    )
    scheduler.run_forever(poll_seconds=10.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
