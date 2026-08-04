"""
Tests for .github/scripts/deploy_roles.py (_discover_services, create_or_update_role).
"""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

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


def _no_such_entity(operation: str) -> ClientError:
    return ClientError({"Error": {"Code": "NoSuchEntity", "Message": "not found"}}, operation)


class TestPolicyMap:
    """Guard against unattachable ARNs sneaking into POLICY_MAP."""

    def test_ce_not_in_policy_map(self, deploy_roles):
        # There is no AWS-managed Cost Explorer policy; a bogus "ce" entry broke
        # the 2026-08-04 deploy_all run. Cost Explorer access comes from
        # lambdas/billing-rest-api/iam_policy.json instead.
        assert "ce" not in deploy_roles.POLICY_MAP

    def test_billing_lambda_has_inline_ce_policy(self, deploy_roles):
        assert (REPO_ROOT / "lambdas" / "billing-rest-api" / "iam_policy.json").exists()


class TestCreateOrUpdateRole:
    """Tests for create_or_update_role() / _role_exists() error scoping."""

    def test_attach_failure_on_existing_role_does_not_create(
        self, deploy_roles, tmp_path, monkeypatch
    ):
        # Regression: a NoSuchEntity from attach_role_policy (bad policy ARN)
        # must not be mistaken for "role missing" and trigger create_role.
        (tmp_path / "app.py").write_text('client = boto3.client("s3")\n')
        fake_iam = MagicMock()
        fake_iam.get_paginator.return_value.paginate.return_value = [{"AttachedPolicies": []}]
        fake_iam.attach_role_policy.side_effect = _no_such_entity("AttachRolePolicy")
        monkeypatch.setattr(deploy_roles, "iam", fake_iam)
        with pytest.raises(ClientError):
            deploy_roles.create_or_update_role("existing-role", "fn", tmp_path)
        fake_iam.create_role.assert_not_called()

    def test_missing_role_is_created_with_policies(self, deploy_roles, tmp_path, monkeypatch):
        (tmp_path / "app.py").write_text('client = boto3.client("s3")\n')
        fake_iam = MagicMock()
        fake_iam.get_role.side_effect = _no_such_entity("GetRole")
        fake_iam.get_paginator.return_value.paginate.return_value = [{"AttachedPolicies": []}]
        monkeypatch.setattr(deploy_roles, "iam", fake_iam)
        monkeypatch.setattr(deploy_roles.time, "sleep", lambda _: None)
        deploy_roles.create_or_update_role("new-role", "fn", tmp_path)
        fake_iam.create_role.assert_called_once()
        attached = {c.kwargs["PolicyArn"] for c in fake_iam.attach_role_policy.call_args_list}
        assert "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole" in attached
        assert deploy_roles.POLICY_MAP["s3"] in attached

    def test_existing_role_attaches_missing_policies(self, deploy_roles, tmp_path, monkeypatch):
        (tmp_path / "app.py").write_text('client = boto3.client("dynamodb")\n')
        fake_iam = MagicMock()
        fake_iam.get_paginator.return_value.paginate.return_value = [{"AttachedPolicies": []}]
        monkeypatch.setattr(deploy_roles, "iam", fake_iam)
        deploy_roles.create_or_update_role("existing-role", "fn", tmp_path)
        fake_iam.create_role.assert_not_called()
        fake_iam.attach_role_policy.assert_called_once_with(
            RoleName="existing-role", PolicyArn=deploy_roles.POLICY_MAP["dynamodb"]
        )
