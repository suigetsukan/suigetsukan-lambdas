#!/usr/bin/env python3
"""
Validate every lambdas/*/config.json against the JSON schema.
Fails the job on first validation error.
"""

import json
import sys
from pathlib import Path

import jsonschema

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / ".github" / "schemas" / "lambda-config.schema.json"
LAMBDAS_DIR = REPO_ROOT / "lambdas"


def load_schema() -> dict:
    if not SCHEMA_PATH.exists():
        print(f"ERROR: Schema not found: {SCHEMA_PATH}")
        sys.exit(1)
    try:
        return json.loads(SCHEMA_PATH.read_text())
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid schema JSON: {e}")
        sys.exit(1)


def validate_config(config_path: Path, schema: dict) -> None:
    print(f"Validating: {config_path}")
    try:
        config = json.loads(config_path.read_text())
    except json.JSONDecodeError as e:
        print(f"  Invalid JSON: {e}")
        sys.exit(1)
    try:
        jsonschema.validate(instance=config, schema=schema)
        print("  Valid")
    except jsonschema.ValidationError as e:
        print("  Validation FAILED:")
        print(f"    Path: {' → '.join(str(p) for p in e.absolute_path)}")
        print(f"    Message: {e.message}")
        sys.exit(1)


def main():
    if not LAMBDAS_DIR.exists():
        print(f"ERROR: lambdas/ directory not found: {LAMBDAS_DIR}")
        sys.exit(1)
    schema = load_schema()
    config_files = list(LAMBDAS_DIR.rglob("config.json"))
    if not config_files:
        print("No config.json files found – nothing to validate")
        return
    print(f"Found {len(config_files)} config.json file(s)")
    print(f"Using schema: {SCHEMA_PATH}")
    for config_path in sorted(config_files):
        validate_config(config_path, schema)
    print("\nAll config.json files are valid!")


if __name__ == "__main__":
    main()
