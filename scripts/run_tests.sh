#!/usr/bin/env bash
#
# run_tests.sh - Single entry point to run all tests (pytest).
#
# Usage:
#   ./scripts/run_tests.sh
#
# Prerequisites:
#   pip install -r requirements-dev.txt (or use .venv - script auto-activates if present)
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [ -f ".venv/bin/activate" ]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
fi

pytest tests/ -v
