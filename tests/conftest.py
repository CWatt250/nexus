"""pytest config for the Nexus regression suite.

Adds ~/AI_Agent to sys.path so `import nexus`, `from tools import ...`
all work without an installed package."""
from __future__ import annotations

import os
os.environ.setdefault("NEXUS_NO_DECISION_LOG", "1")  # keep router traffic log real-only
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
