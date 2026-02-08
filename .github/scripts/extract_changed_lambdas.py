#!/usr/bin/env python3
"""
Extract changed Lambda names from paths-filter output.
Input: CHANGED_FILES (space/newline-separated list of file paths)
Output: GitHub Actions output `changed_lambdas` as JSON array
"""

import json
import os
import sys
from pathlib import Path


def main():
    changed_files = os.getenv("CHANGED_FILES", "").strip()
    if not changed_files:
        print("changed_lambdas=[]")
        sys.exit(0)

    paths = [p.strip() for p in changed_files.split() if p.strip()]
    lambda_names = set()

    for p in paths:
        try:
            path = Path(p)
            if path.parts[0] == "lambdas" and len(path.parts) > 1:
                lambda_names.add(path.parts[1])
        except (ValueError, IndexError, OSError):
            continue

    result = json.dumps(sorted(lambda_names))
    print(f"changed_lambdas={result}")


if __name__ == "__main__":
    main()
