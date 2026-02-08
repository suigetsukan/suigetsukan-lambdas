#!/usr/bin/env python3
"""
Install pre-commit hooks for MISRA-inspired coding standards.
Run from repo root: python scripts/install-pre-commit.sh
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main():
    print("Installing pre-commit hooks...")
    print("  These will run ruff, mypy, bandit, and pytest on commit.")
    result = subprocess.run(
        [sys.executable, "-m", "pre_commit", "install"],
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        sys.exit(1)
    print("Done. Run 'pre-commit run --all-files' to verify.")


if __name__ == "__main__":
    main()
