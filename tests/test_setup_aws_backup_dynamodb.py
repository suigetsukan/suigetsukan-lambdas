"""
Tests for scripts/setup_aws_backup_dynamodb.py (mocked boto3).
"""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from botocore.exceptions import ClientError

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "setup_aws_backup_dynamodb.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("setup_aws_backup_dynamodb", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestGetAccountId:
    def test_returns_account_from_sts(self):
        module = _load_module()
        session = MagicMock()
        sts = MagicMock()
        sts.get_caller_identity.return_value = {"Account": "123456789012"}
        session.client.return_value = sts

        result = module.get_account_id(session)

        assert result == "123456789012"
        session.client.assert_called_once_with("sts", region_name=module.REGION)


class TestGetBackupRoleArn:
    def test_returns_arn_when_role_exists(self):
        module = _load_module()
        session = MagicMock()
        iam = MagicMock()
        iam.get_role.return_value = {}
        session.client.return_value = iam

        result = module.get_backup_role_arn(session, "123456789012")

        assert result == f"arn:aws:iam::123456789012:role/{module.BACKUP_ROLE_NAME}"
        iam.get_role.assert_called_once_with(RoleName=module.BACKUP_ROLE_NAME)

    def test_raises_when_role_not_found(self):
        module = _load_module()
        session = MagicMock()
        iam = MagicMock()
        iam.get_role.side_effect = ClientError(
            {"Error": {"Code": "NoSuchEntity", "Message": "Role not found"}}, "GetRole"
        )
        session.client.return_value = iam

        with pytest.raises(ClientError):
            module.get_backup_role_arn(session, "123456789012")


class TestFindPlanByName:
    def test_returns_plan_id_when_name_matches(self):
        module = _load_module()
        backup = MagicMock()
        backup.get_paginator.return_value.paginate.return_value = [
            {"BackupPlansList": [{"BackupPlanId": "plan-abc"}]}
        ]
        backup.get_backup_plan.return_value = {
            "BackupPlan": {"BackupPlanName": module.PLAN_NAME}
        }

        result = module.find_plan_by_name(backup)

        assert result == "plan-abc"

    def test_returns_none_when_no_plan_matches(self):
        module = _load_module()
        backup = MagicMock()
        backup.get_paginator.return_value.paginate.return_value = [
            {"BackupPlansList": [{"BackupPlanId": "plan-xyz"}]}
        ]
        backup.get_backup_plan.return_value = {
            "BackupPlan": {"BackupPlanName": "Other-Plan"}
        }

        result = module.find_plan_by_name(backup)

        assert result is None

    def test_returns_none_when_list_empty(self):
        module = _load_module()
        backup = MagicMock()
        backup.get_paginator.return_value.paginate.return_value = [
            {"BackupPlansList": []}
        ]

        result = module.find_plan_by_name(backup)

        assert result is None


class TestCreateBackupPlan:
    def test_returns_backup_plan_id(self):
        module = _load_module()
        backup = MagicMock()
        backup.create_backup_plan.return_value = {"BackupPlanId": "plan-new-123"}

        result = module.create_backup_plan(backup)

        assert result == "plan-new-123"
        backup.create_backup_plan.assert_called_once()
        call_kw = backup.create_backup_plan.call_args[1]
        assert call_kw["BackupPlan"]["BackupPlanName"] == module.PLAN_NAME
        assert "Rules" in call_kw["BackupPlan"]


class TestSelectionExists:
    def test_returns_true_when_selection_found(self):
        module = _load_module()
        backup = MagicMock()
        backup.get_paginator.return_value.paginate.return_value = [
            {"BackupSelectionsList": [{"SelectionName": module.SELECTION_NAME}]}
        ]

        result = module.selection_exists(backup, "plan-123")

        assert result is True

    def test_returns_false_when_selection_not_found(self):
        module = _load_module()
        backup = MagicMock()
        backup.get_paginator.return_value.paginate.return_value = [
            {"BackupSelectionsList": [{"SelectionName": "OtherSelection"}]}
        ]

        result = module.selection_exists(backup, "plan-123")

        assert result is False

    def test_returns_false_when_list_empty(self):
        module = _load_module()
        backup = MagicMock()
        backup.get_paginator.return_value.paginate.return_value = [
            {"BackupSelectionsList": []}
        ]

        result = module.selection_exists(backup, "plan-123")

        assert result is False


class TestCreateBackupSelection:
    def test_calls_create_backup_selection_with_expected_args(self):
        module = _load_module()
        backup = MagicMock()
        role_arn = "arn:aws:iam::123456789012:role/AWSBackupDefaultServiceRole"
        account_id = "123456789012"

        module.create_backup_selection(backup, "plan-123", role_arn, account_id)

        backup.create_backup_selection.assert_called_once()
        call_kw = backup.create_backup_selection.call_args[1]
        assert call_kw["BackupPlanId"] == "plan-123"
        assert call_kw["BackupSelection"]["SelectionName"] == module.SELECTION_NAME
        assert call_kw["BackupSelection"]["IamRoleArn"] == role_arn
        assert "arn:aws:dynamodb:" in call_kw["BackupSelection"]["Resources"][0]
        assert ":table/*" in call_kw["BackupSelection"]["Resources"][0]
