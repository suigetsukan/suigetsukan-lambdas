"""
Pytest configuration and fixtures for suigetsukan-lambdas.
"""

import os
import sys
from pathlib import Path

# Add repo root and lambda dirs to path so imports work (before any lambda imports)
REPO_ROOT = Path(__file__).resolve().parent.parent
FILE_NAME_DECIPHER = REPO_ROOT / "lambdas" / "file-name-decipher"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(FILE_NAME_DECIPHER))

# Set env vars required by file-name-decipher (must be set before importing aikido/battodo/danzan_ryu)
os.environ.setdefault("AWS_DDB_AIKIDO_TABLE_NAME", "test-aikido-table")
os.environ.setdefault("AWS_DDB_BATTODO_TABLE_NAME", "test-battodo-table")
os.environ.setdefault("AWS_DDB_DANZAN_RYU_TABLE_NAME", "test-danzan-ryu-table")
