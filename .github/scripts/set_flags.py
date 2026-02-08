#!/usr/bin/env python3
"""
Set:
  config_changed → true if any config.json changed
  deploy_all → true if ANY of the critical files changed
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
    lambdas_files = os.getenv("LAMBDAS_FILES", "")
    common_files = os.getenv("COMMON_FILES", "")
    layers_files = os.getenv("LAYERS_FILES", "")
    critical_files = os.getenv("CRITICAL_FILES", "").split()
    config_changed = any("config.json" in f for f in lambdas_files.split())
    deploy_all_commit = os.getenv("DEPLOY_ALL_COMMIT", "false").lower() == "true"
    deploy_all = (
        any(f in CRITICAL_FILES for f in critical_files)
        or deploy_all_commit
        or bool(layers_files.strip())
        or bool(common_files.strip())
    )
    print(f"config_changed={'true' if config_changed else 'false'}")
    print(f"deploy_all={'true' if deploy_all else 'false'}")


if __name__ == "__main__":
    main()
