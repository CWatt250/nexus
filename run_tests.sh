#!/usr/bin/env bash
# run_tests.sh — full Nexus regression suite.
# Used by the nightly nexus-test.timer (Phase 14.5) and on demand.
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${NEXUS_PYTHON:-/home/cwatt250/AI_Agent/venv/bin/python3}"

echo "[run_tests] python: $PYTHON"
echo "[run_tests] pytest version:"
"$PYTHON" -m pytest --version

# -ra surfaces skip/xfail reasons; --tb=short keeps logs scannable.
"$PYTHON" -m pytest tests/ -ra --tb=short --color=no "$@"
