#!/usr/bin/env python3
"""
Set deploy_all → true if ANY of the critical files changed, layers changed,
common changed, or commit message contains deploy_all.
"""

import os

CRITICAL_FILES = [
    ".github/schemas/lambda-config.schema.json",
    ".github/scripts/deploy_lambdas.py",
    ".github/scripts/extract_changed_lambdas.py",
    ".github/scripts/set_flags.py",
    ".github/scripts/validate_lambda_configs.py",
    ".github/workflows/deploy.yml",
]


def main():
    common_files = os.getenv("COMMON_FILES", "")
    layers_files = os.getenv("LAYERS_FILES", "")
    critical_files = os.getenv("CRITICAL_FILES", "").split()
    deploy_all_commit = os.getenv("DEPLOY_ALL_COMMIT", "false").lower() == "true"
    deploy_all = (
        any(f in CRITICAL_FILES for f in critical_files)
        or deploy_all_commit
        or bool(layers_files.strip())
        or bool(common_files.strip())
    )
    print(f"deploy_all={'true' if deploy_all else 'false'}")


if __name__ == "__main__":
    main()
