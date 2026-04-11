#!/usr/bin/env bash
#
# test_then_push.sh - Run tests; if they pass, pull --rebase and push.
# Does not run git add or git commit (assumes you have already committed).
#
# Usage:
#   ./scripts/test_then_push.sh
#
# Prerequisites: same as run_tests.sh (deps or .venv).
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

echo "=== Running tests ==="
./scripts/run_tests.sh

echo "=== Pull with rebase ==="
git pull --rebase

echo "=== Push ==="
git push
