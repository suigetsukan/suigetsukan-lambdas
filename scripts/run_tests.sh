#!/usr/bin/env bash
#
# run_tests.sh - Single entry point to run all tests (lint, format, mypy, bandit, config validation, pytest).
# Matches CI lint-and-test + validate-config so local verification catches the same failures.
#
# Usage:
#   ./scripts/run_tests.sh
#
# Prerequisites:
#   pip install -r requirements-dev.txt (and pip install boto3); or use .venv (script auto-activates if present).
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Auto-create a per-checkout .venv if missing (so this works in fresh git
# worktrees, which share .git but not workspace files). Prefer python3 with
# python fallback so we don't depend on a `python` symlink (macOS Homebrew).
if [ ! -f ".venv/bin/activate" ]; then
  if command -v python3 >/dev/null 2>&1; then
    py_boot=python3
  elif command -v python >/dev/null 2>&1; then
    py_boot=python
  else
    echo "Error: neither python3 nor python is on PATH; cannot create .venv." >&2
    exit 1
  fi
  echo "No .venv found at $REPO_ROOT/.venv — creating one with $py_boot -m venv..."
  "$py_boot" -m venv .venv
  PIP_DISABLE_PIP_VERSION_CHECK=1 .venv/bin/python -m pip install -q -r requirements-dev.txt boto3
fi
# shellcheck source=/dev/null
source .venv/bin/activate

echo "=== Ruff (lint) ==="
ruff check . --fix

echo "=== Ruff (format check) ==="
ruff format --check .

echo "=== Mypy (type check) ==="
mypy .

echo "=== Bandit (security) ==="
bandit -r lambdas common .github/scripts -ll -c pyproject.toml

echo "=== Validate lambda configs ==="
python .github/scripts/validate_lambda_configs.py

echo "=== Pytest ==="
pytest tests/ -v
