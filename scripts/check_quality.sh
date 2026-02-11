#!/usr/bin/env bash
#
# check_quality.sh - Run all strict code quality checks (single source of truth)
#
# Used by both local pre-commit and CI (.github/workflows/pipeline.yml).
# Run before commit to catch issues before CI fails.
# Prerequisites: pip install -r requirements.txt (jsonschema, lizard/xenon, etc.)
#
# Usage:
#   ./scripts/check_quality.sh
#
# Prerequisites:
#   pip install -r requirements.txt  (or use .venv - script auto-activates if present)
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Use venv if present (ensures all tools have required deps)
if [ -f ".venv/bin/activate" ]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Code Quality Checks (matches pipeline Safety)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

run() {
  echo "▶ $1"
  "$@"
  echo ""
}

run pylint lambdas/ --fail-under=8.5
run pytest tests/ --cov=lambdas --cov-report=term-missing --cov-report=html
# Run mypy per-lambda to avoid "duplicate module app" (each lambda has app.py)
# Use mypy-lambdas.ini so lambdas/ is not excluded (main pyproject excludes it).
for d in lambdas/*/; do
  [ -f "${d}app.py" ] || continue
  run mypy --config-file=mypy-lambdas.ini "$d"
done
run ruff check lambdas/
run bandit -r lambdas/ -ll
# Ignore ecdsa vulns 64459, 64396: no fix available (python-jose transitive dep)
# Check only project deps so we don't fail on system/homebrew packages.
run safety check -r requirements-dev.txt --json --ignore 64459,64396

# Config validation (uses jsonschema)
echo "▶ Validate lambda configs"
python3 .github/scripts/validate_lambda_configs.py
echo ""

# MISRA-like: CCN <= 10, params <= 7 (run last)
# Prefer lizard (CCN + params); fallback to xenon (CCN only, Python-native)
echo "▶ Complexity check (CCN ≤10, params ≤7)"
if command -v lizard &>/dev/null; then
  lizard lambdas/ -l python -C 10 -a 7
else
  if command -v xenon &>/dev/null; then
    echo "lizard not found, using xenon (CCN ≤10 only, no param check)"
    xenon --max-absolute=B lambdas/
  else
    echo "Error: Neither lizard nor xenon available. Install: pip install lizard"
    exit 1
  fi
fi
echo ""

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅ All quality checks passed"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
