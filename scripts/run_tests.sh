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

if [ -f ".venv/bin/activate" ]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
fi

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
