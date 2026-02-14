#!/usr/bin/env python3
"""Validate .mdc rule files: YAML frontmatter and required fields."""

import argparse
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("error: pyyaml required (pip install pyyaml)")
    sys.exit(2)


def extract_frontmatter(content: str) -> tuple:
    """Extract YAML frontmatter and body. Returns (frontmatter_str, body)."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", content, re.DOTALL)
    if not match:
        return None, content
    return match.group(1), match.group(2) or ""


def _is_globs_non_empty(globs) -> bool:
    """Check globs is non-empty. Accepts string or list (Cursor supports both)."""
    if globs is None:
        return False
    if isinstance(globs, list):
        return len(globs) > 0 and all(
            isinstance(g, str) and g.strip() for g in globs
        )
    return bool(str(globs).strip())


def validate_mdc(path: Path) -> list[str]:
    """Validate a single .mdc file. Returns list of error messages."""
    errors = []
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        return [f"Could not read: {e}"]

    frontmatter_str, _ = extract_frontmatter(content)
    if frontmatter_str is None:
        return ["Missing or invalid YAML frontmatter (expected --- ... --- block at start)"]

    try:
        fm = yaml.safe_load(frontmatter_str)
    except yaml.YAMLError as e:
        return [f"Invalid YAML: {e}"]

    if not isinstance(fm, dict):
        errors.append("Frontmatter must be a YAML object")
        return errors

    if "description" not in fm:
        errors.append("Missing required field: description")
    elif not fm.get("description") or not str(fm["description"]).strip():
        errors.append("description must be non-empty")

    if fm.get("alwaysApply") is not True and "globs" not in fm:
        errors.append("Must have either alwaysApply: true or globs")
    elif "globs" in fm and not _is_globs_non_empty(fm.get("globs")):
        errors.append("globs must be non-empty when present (currently empty)")

    return errors


def main() -> int:
    default_rules = Path(__file__).resolve().parent.parent / "rules"
    rules_dir = Path(
        os.environ.get("RULES_DIR", str(default_rules))
    ).resolve()

    parser = argparse.ArgumentParser(
        description="Validate .mdc rule files (YAML frontmatter, required fields)"
    )
    parser.add_argument(
        "--rules-dir",
        type=Path,
        default=rules_dir,
        help=f"Directory containing .mdc files (default: RULES_DIR or {default_rules})",
    )
    args = parser.parse_args()
    rules_dir = args.rules_dir.resolve()

    if not rules_dir.exists():
        print(f"error: rules directory not found: {rules_dir}")
        return 2

    mdc_files = sorted(rules_dir.glob("*.mdc"))
    if not mdc_files:
        print("error: no .mdc files in rules/")
        return 2

    all_errors = []
    for path in mdc_files:
        errs = validate_mdc(path)
        if errs:
            for e in errs:
                all_errors.append(f"{path}: {e}")

    if all_errors:
        for e in all_errors:
            print(e)
        return 1

    print(f"OK: validated {len(mdc_files)} rule file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
