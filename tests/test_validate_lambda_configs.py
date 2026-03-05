"""
Tests for .github/scripts/validate_lambda_configs.py (load_schema, validate_config).
"""

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / ".github" / "scripts" / "validate_lambda_configs.py"

# Minimal schema sufficient to validate configs that have function_name and handler
MINIMAL_SCHEMA = {
    "type": "object",
    "properties": {
        "function_name": {"type": "string"},
        "handler": {"type": "string"},
    },
    "required": ["function_name", "handler"],
}


def _load_module():
    spec = importlib.util.spec_from_file_location("validate_lambda_configs", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestLoadSchema:
    """Tests for load_schema()."""

    def test_load_schema_returns_dict(self, tmp_path, monkeypatch):
        schema_file = tmp_path / "lambda-config.schema.json"
        schema_file.write_text(json.dumps(MINIMAL_SCHEMA))
        module = _load_module()
        monkeypatch.setattr(module, "SCHEMA_PATH", schema_file)
        result = module.load_schema()
        assert isinstance(result, dict)
        assert result["type"] == "object"
        assert "function_name" in result["properties"]

    def test_load_schema_missing_file_exits(self, tmp_path, monkeypatch):
        missing = tmp_path / "nonexistent.json"
        module = _load_module()
        monkeypatch.setattr(module, "SCHEMA_PATH", missing)
        with pytest.raises(SystemExit):
            module.load_schema()

    def test_load_schema_invalid_json_exits(self, tmp_path, monkeypatch):
        bad_schema = tmp_path / "bad.json"
        bad_schema.write_text("{ invalid }")
        module = _load_module()
        monkeypatch.setattr(module, "SCHEMA_PATH", bad_schema)
        with pytest.raises(SystemExit):
            module.load_schema()


class TestValidateConfig:
    """Tests for validate_config()."""

    def test_validate_config_valid_passes(self, tmp_path):
        module = _load_module()
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"function_name": "test-fn", "handler": "app.handler"}))
        module.validate_config(config_file, MINIMAL_SCHEMA)
        # No exit

    def test_validate_config_invalid_json_exits(self, tmp_path):
        module = _load_module()
        config_file = tmp_path / "config.json"
        config_file.write_text("{ not valid json")
        with pytest.raises(SystemExit):
            module.validate_config(config_file, MINIMAL_SCHEMA)

    def test_validate_config_schema_violation_exits(self, tmp_path):
        module = _load_module()
        config_file = tmp_path / "config.json"
        # Missing required "handler"
        config_file.write_text(json.dumps({"function_name": "test-fn"}))
        with pytest.raises(SystemExit):
            module.validate_config(config_file, MINIMAL_SCHEMA)
