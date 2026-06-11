#!/usr/bin/env bash
# Phase 39 — eval harness entrypoint. Exits nonzero on any failure.
# Every future phase must run this before its ship gate counts as passed.
set -euo pipefail
exec "$HOME/AI_Agent/venv/bin/python3" "$HOME/AI_Agent/tests/evals/run_evals.py" "$@"
