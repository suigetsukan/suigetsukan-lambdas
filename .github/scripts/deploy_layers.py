#!/usr/bin/env python3
"""
Deploy Lambda layers (if any exist).
Skips gracefully when layers/ is empty or absent.
"""

import sys
from pathlib import Path

REPO_ROOT = Path.cwd()
LAYERS_DIR = REPO_ROOT / "layers"

if not LAYERS_DIR.exists() or not any(LAYERS_DIR.iterdir()):
    print("No layers to deploy – skipping")
    sys.exit(0)

# TODO: Add layer build/deploy logic when needed (e.g. shared deps)
print("Layers directory exists but deploy not yet implemented – skipping")
sys.exit(0)
