"""
Pytest configuration and fixtures for suigetsukan-lambdas.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

# Add repo root and lambda dirs to path so imports work (before any lambda imports)
REPO_ROOT = Path(__file__).resolve().parent.parent
FILE_NAME_DECIPHER = REPO_ROOT / "lambdas" / "file-name-decipher"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(FILE_NAME_DECIPHER))

# Set env vars required by file-name-decipher (must be set before importing aikido/battodo/danzan_ryu)
os.environ.setdefault("AWS_DDB_AIKIDO_TABLE_NAME", "test-aikido-table")
os.environ.setdefault("AWS_DDB_BATTODO_TABLE_NAME", "test-battodo-table")
os.environ.setdefault("AWS_DDB_DANZAN_RYU_TABLE_NAME", "test-danzan-ryu-table")


def _load_lambda_module(lambda_name: str):
    """Load app module from a lambda directory."""
    lambda_dir = REPO_ROOT / "lambdas" / lambda_name
    app_path = lambda_dir / "app.py"
    if not app_path.exists():
        raise FileNotFoundError(f"No app.py in {lambda_dir}")
    lambda_dir_str = str(lambda_dir)
    if lambda_dir_str not in sys.path:
        sys.path.insert(0, lambda_dir_str)
    spec = importlib.util.spec_from_file_location(f"app_{lambda_name}", app_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def load_lambda():
    """Fixture to load a lambda's app module by directory name."""
    return _load_lambda_module
