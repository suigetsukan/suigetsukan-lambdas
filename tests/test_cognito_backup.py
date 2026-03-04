"""
Tests for cognito-backup Lambda: export users and group memberships to S3,
with validation, manifest, and CloudWatch metrics.
"""

import gzip
import io
import importlib.util
import json
from datetime import datetime, UTC
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
COGNITO_BACKUP_APP = REPO_ROOT / "lambdas" / "cognito-backup" / "app.py"


def _load_cognito_backup_app():
    spec = importlib.util.spec_from_file_location("cognito_backup_app", COGNITO_BACKUP_APP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_mock_cognito_one_user():
    mock_cognito = MagicMock()
    mock_cognito.list_users.side_effect = [
        {
            "Users": [
                {
                    "Username": "user1",
                    "Attributes": [{"Name": "email", "Value": "u1@example.com"}],
                    "UserCreateDate": datetime(2024, 1, 1, tzinfo=UTC),
                    "UserLastModifiedDate": datetime(2024, 1, 2, tzinfo=UTC),
                    "Enabled": True,
                    "UserStatus": "CONFIRMED",
                },
            ],
            "PaginationToken": None,
        },
    ]
    mock_cognito.admin_list_groups_for_user.return_value = {"Groups": [{"GroupName": "approved"}]}
    mock_cognito.list_groups.return_value = {"Groups": [{"GroupName": "approved"}]}
    mock_cognito.describe_user_pool.return_value = {
        "UserPool": {
            "Name": "test-pool",
            "CreationDate": datetime(2024, 1, 1, tzinfo=UTC),
            "LastModifiedDate": datetime(2024, 1, 2, tzinfo=UTC),
            "MfaConfiguration": "OFF",
            "AccountRecoverySetting": None,
        }
    }
    return mock_cognito


def _make_mock_s3_with_storage():
    s3_storage = {}

    def put_object(**kwargs):
        body = kwargs["Body"]
        s3_storage[kwargs["Key"]] = body.getvalue() if hasattr(body, "getvalue") else body

    def get_object(Bucket, Key, **kwargs):
        body = s3_storage.get(Key)
        if body is None:
            raise KeyError(Key)
        return {"Body": io.BytesIO(body) if isinstance(body, bytes) else io.BytesIO(body)}

    def head_object(Bucket, Key, **kwargs):
        body = s3_storage.get(Key, b"x")
        size = len(body) if isinstance(body, bytes) else 1
        return {"ContentLength": size}

    mock_s3 = MagicMock()
    mock_s3.put_object.side_effect = put_object
    mock_s3.get_object.side_effect = get_object
    mock_s3.head_object.side_effect = head_object
    return mock_s3, s3_storage


def _assert_backup_success(result, mock_s3, mock_cloudwatch):
    assert result["status"] == "success"
    assert result["backup_key"].startswith("backups/")
    assert result["backup_key"].endswith(".json.gz")
    put_calls = mock_s3.put_object.call_args_list
    assert len(put_calls) >= 2
    backup_call = put_calls[0]
    assert backup_call.kwargs["Bucket"] == "my-backup-bucket"
    assert backup_call.kwargs["Key"] == result["backup_key"]
    assert backup_call.kwargs["ContentType"] == "application/gzip"
    body_bytes = backup_call.kwargs["Body"]
    body_bytes = body_bytes.getvalue() if hasattr(body_bytes, "getvalue") else body_bytes
    data = json.loads(gzip.decompress(body_bytes).decode("utf-8"))
    assert data["COGNITO_USER_POOL_ID"] == "us-west-1_abc123"
    assert data["total_users"] == 1
    assert len(data["users"]) == 1
    assert data["users"][0]["Username"] == "user1"
    assert data["users"][0]["Attributes"]["email"] == "u1@example.com"
    assert data["users"][0]["Groups"] == ["approved"]
    assert "timestamp" in data and "groups" in data and "pool_metadata" in data
    assert put_calls[1].kwargs["Key"] == "backups/latest/manifest.json"
    manifest_body = json.loads(put_calls[1].kwargs["Body"].decode("utf-8"))
    assert manifest_body["backup_key"] == result["backup_key"] and manifest_body["total_users"] == 1
    get_calls = [
        c for c in mock_s3.get_object.call_args_list if c.kwargs.get("Key") == result["backup_key"]
    ]
    assert len(get_calls) >= 1
    call_kw = mock_cloudwatch.put_metric_data.call_args.kwargs
    assert call_kw["Namespace"] == "CognitoBackup"
    assert "TotalUsers" in [m["MetricName"] for m in call_kw["MetricData"]]
    assert "ExecutionDuration" in [m["MetricName"] for m in call_kw["MetricData"]]


def test_lambda_handler_exports_and_validates_backup():
    """Exports users and groups to S3 at backups/.../cognito-users-*.json.gz; validates and updates manifest."""
    mock_cognito = _make_mock_cognito_one_user()
    mock_s3, _ = _make_mock_s3_with_storage()
    mock_cloudwatch = MagicMock()
    with patch.dict(
        "os.environ",
        {
            "AWS_REGION": "us-west-1",
            "AWS_COGNITO_USER_POOL_ID": "us-west-1_abc123",
            "AWS_S3_BACKUP_BUCKET": "my-backup-bucket",
        },
    ):
        app = _load_cognito_backup_app()
        with patch.object(
            app,
            "_get_clients",
            return_value={
                "cognito": mock_cognito,
                "s3": mock_s3,
                "sns": MagicMock(),
                "cloudwatch": mock_cloudwatch,
            },
        ):
            result = app.lambda_handler({}, MagicMock())
    _assert_backup_success(result, mock_s3, mock_cloudwatch)


def test_lambda_handler_raises_when_env_missing():
    """Raises ValueError when AWS_COGNITO_USER_POOL_ID or AWS_S3_BACKUP_BUCKET unset."""
    with (
        patch.dict(
            "os.environ",
            {"AWS_REGION": "us-west-1", "AWS_COGNITO_USER_POOL_ID": "", "AWS_S3_BACKUP_BUCKET": ""},
            clear=False,
        ),
    ):
        app = _load_cognito_backup_app()
        with pytest.raises(ValueError, match="AWS_COGNITO_USER_POOL_ID and AWS_S3_BACKUP_BUCKET"):
            app.lambda_handler({}, MagicMock())


def test_validation_failure_raises_and_sns_called_when_configured():
    """When backup validation fails (e.g. corrupt get_object), handler raises; SNS published if topic set."""
    mock_cognito = MagicMock()
    mock_cognito.list_users.return_value = {"Users": [], "PaginationToken": None}
    mock_cognito.list_groups.return_value = {"Groups": []}
    mock_cognito.describe_user_pool.return_value = {
        "UserPool": {"Name": "x", "MfaConfiguration": "OFF"}
    }

    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = None
    mock_s3.head_object.return_value = {"ContentLength": 100}
    # get_object returns invalid/corrupt data so validation fails
    mock_s3.get_object.return_value = {"Body": io.BytesIO(b"not-valid-gzip-or-json")}

    mock_sns = MagicMock()

    app = _load_cognito_backup_app()
    with (
        patch.dict(
            "os.environ",
            {
                "AWS_REGION": "us-west-1",
                "AWS_COGNITO_USER_POOL_ID": "us-west-1_xyz",
                "AWS_S3_BACKUP_BUCKET": "bucket",
                "SNS_SUPPORT_TOPIC_ARN": "arn:aws:sns:us-west-1:123:backup-fail",
            },
        ),
        patch.object(app, "_get_clients") as mock_get_clients,
    ):
        mock_get_clients.return_value = {
            "cognito": mock_cognito,
            "s3": mock_s3,
            "sns": mock_sns,
            "cloudwatch": MagicMock(),
        }
        with pytest.raises(ValueError, match="Backup decompress or JSON parse failed"):
            app.lambda_handler({}, MagicMock())

    mock_sns.publish.assert_called_once()
    call_kw = mock_sns.publish.call_args.kwargs
    assert "Cognito Backup Failure" in call_kw["Subject"]
    assert "us-west-1_xyz" in call_kw["Message"]
