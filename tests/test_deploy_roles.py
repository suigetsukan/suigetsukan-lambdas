"""
Tests for .github/scripts/deploy_roles.py (_discover_services).
"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / ".github" / "scripts" / "deploy_roles.py"


@pytest.fixture
def deploy_roles(monkeypatch):
    # Module creates boto3.client("iam") at import time; a region must be
    # resolvable or botocore raises NoRegionError in bare CI environments.
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    spec = importlib.util.spec_from_file_location("deploy_roles", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestDiscoverServices:
    """Tests for _discover_services()."""

    def test_simple_client_call(self, deploy_roles, tmp_path):
        (tmp_path / "app.py").write_text('client = boto3.client("dynamodb")\n')
        assert deploy_roles._discover_services(tmp_path) == {"dynamodb"}

    def test_client_call_with_trailing_args(self, deploy_roles, tmp_path):
        (tmp_path / "app.py").write_text('client = boto3.client("s3", region_name=region)\n')
        assert deploy_roles._discover_services(tmp_path) == {"s3"}

    def test_multiline_client_call(self, deploy_roles, tmp_path):
        # Shape of lambdas/contributor-submission-manager/presign.py, which the
        # old regex silently missed (role deployed with no S3 policy).
        (tmp_path / "presign.py").write_text(
            "client = boto3.client(\n"
            '    "s3",\n'
            "    region_name=region,\n"
            '    config=boto3.session.Config(signature_version="s3v4"),\n'
            ")\n"
        )
        assert deploy_roles._discover_services(tmp_path) == {"s3"}

    def test_resource_call_with_trailing_args(self, deploy_roles, tmp_path):
        (tmp_path / "app.py").write_text(
            "db = boto3.resource('dynamodb', region_name='us-east-1')\n"
        )
        assert deploy_roles._discover_services(tmp_path) == {"dynamodb"}

    def test_multiple_services_across_files(self, deploy_roles, tmp_path):
        (tmp_path / "app.py").write_text(
            'ses = boto3.client("ses")\ndb = boto3.resource(\n    "dynamodb",\n)\n'
        )
        (tmp_path / "presign.py").write_text('s3 = boto3.client("s3", region_name="us-west-1")\n')
        assert deploy_roles._discover_services(tmp_path) == {"ses", "dynamodb", "s3"}

    def test_no_boto3_calls(self, deploy_roles, tmp_path):
        (tmp_path / "app.py").write_text("print('no aws here')\n")
        assert deploy_roles._discover_services(tmp_path) == set()
