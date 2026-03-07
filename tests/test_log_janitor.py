"""Tests for log-janitor Lambda."""

import json
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError


def _make_paginator(pages):
    """Return a mock paginator that yields the given pages."""

    def paginate(**_kwargs):
        yield from pages

    pag = MagicMock()
    pag.paginate = paginate
    return pag


@patch.dict(
    "os.environ",
    {
        "MODE": "AUDIT",
        "REGIONS": "us-east-2",
        "LOG_GROUP_INCLUDE_PREFIXES": "/aws/lambda/",
        "LOG_GROUP_EXCLUDE_PATTERNS": "",
        "LOG_GROUP_EXCEPTIONS_JSON": "{}",
        "ENABLE_ALARMS": "false",
        "ENABLE_DASHBOARD": "false",
        "ENABLE_CLOUDTRAIL_TRIPWIRES": "false",
        "ENABLE_CLOUDTRAIL_S3_POSTURE": "true",
    },
    clear=False,
)
@patch("boto3.client")
def test_audit_mode_reports_drift_no_put_retention(mock_boto_client, load_lambda):
    """AUDIT mode: drift reported, PutRetentionPolicy not called."""
    mock_logs = MagicMock()
    mock_logs.get_paginator.return_value = _make_paginator([
        {
            "logGroups": [
                {
                    "logGroupName": "/aws/lambda/foo",
                    "retentionInDays": None,
                }
            ]
        }
    ])
    mock_ct = MagicMock()
    mock_ct.describe_trails.return_value = {"trailList": [{"Name": "t1", "IsMultiRegionTrail": True, "S3BucketName": "b1", "LogFileValidationEnabled": True}]}
    mock_ct.get_trail_status.return_value = {"IsLogging": True}
    mock_s3 = MagicMock()
    mock_s3.get_public_access_block.return_value = {
        "PublicAccessBlockConfiguration": {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        }
    }
    mock_s3.get_bucket_versioning.return_value = {"Status": "Enabled"}
    mock_s3.get_bucket_encryption.return_value = {
        "ServerSideEncryptionConfiguration": {"Rules": [{}]}
    }
    mock_s3.get_bucket_lifecycle_configuration.return_value = {"Rules": [{}]}

    def client(svc, **_kw):
        if svc == "logs":
            return mock_logs
        if svc == "cloudtrail":
            return mock_ct
        if svc == "s3":
            return mock_s3
        return MagicMock()

    mock_boto_client.side_effect = client

    mod = load_lambda("log-janitor")
    result = mod.lambda_handler({}, None)

    assert "execution_metadata" in result
    assert result["execution_metadata"]["mode"] == "AUDIT"
    assert result["findings"]["log_groups"]["scanned"] == 1
    assert result["findings"]["log_groups"]["in_scope"] == 1
    assert result["findings"]["log_groups"]["drifted"] == 1
    assert result["findings"]["log_groups"]["fixed"] == 0
    mock_logs.put_retention_policy.assert_not_called()


@patch.dict(
    "os.environ",
    {
        "MODE": "APPLY",
        "REGIONS": "us-east-2",
        "LOG_GROUP_INCLUDE_PREFIXES": "/aws/lambda/",
        "LOG_GROUP_EXCLUDE_PATTERNS": "",
        "LOG_GROUP_EXCEPTIONS_JSON": "{}",
        "ENABLE_ALARMS": "false",
        "ENABLE_DASHBOARD": "false",
        "ENABLE_CLOUDTRAIL_TRIPWIRES": "false",
        "ENABLE_CLOUDTRAIL_S3_POSTURE": "true",
    },
    clear=False,
)
@patch("boto3.client")
def test_apply_mode_calls_put_retention(mock_boto_client, load_lambda):
    """APPLY mode: PutRetentionPolicy called and fixed count incremented."""
    mock_logs = MagicMock()
    mock_logs.get_paginator.return_value = _make_paginator([
        {
            "logGroups": [
                {
                    "logGroupName": "/aws/lambda/bar",
                    "retentionInDays": None,
                }
            ]
        }
    ])
    mock_ct = MagicMock()
    mock_ct.describe_trails.return_value = {"trailList": [{"Name": "t1", "IsMultiRegionTrail": True, "S3BucketName": "b1", "LogFileValidationEnabled": True}]}
    mock_ct.get_trail_status.return_value = {"IsLogging": True}
    mock_s3 = MagicMock()
    mock_s3.get_public_access_block.return_value = {
        "PublicAccessBlockConfiguration": {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        }
    }
    mock_s3.get_bucket_versioning.return_value = {"Status": "Enabled"}
    mock_s3.get_bucket_encryption.return_value = {
        "ServerSideEncryptionConfiguration": {"Rules": [{}]}
    }
    mock_s3.get_bucket_lifecycle_configuration.return_value = {"Rules": [{}]}

    def client(svc, **_kw):
        if svc == "logs":
            return mock_logs
        if svc == "cloudtrail":
            return mock_ct
        if svc == "s3":
            return mock_s3
        return MagicMock()

    mock_boto_client.side_effect = client

    mod = load_lambda("log-janitor")
    result = mod.lambda_handler({}, None)

    assert result["findings"]["log_groups"]["drifted"] == 1
    assert result["findings"]["log_groups"]["fixed"] == 1
    mock_logs.put_retention_policy.assert_called_once()
    call = mock_logs.put_retention_policy.call_args
    assert call.kwargs["logGroupName"] == "/aws/lambda/bar"
    assert call.kwargs["retentionInDays"] == 90


@patch.dict(
    "os.environ",
    {
        "MODE": "AUDIT",
        "REGIONS": "us-east-2",
        "REQUIRE_CLOUDTRAIL": "true",
        "ENABLE_ALARMS": "false",
        "ENABLE_DASHBOARD": "false",
        "ENABLE_CLOUDTRAIL_TRIPWIRES": "false",
        "ENABLE_CLOUDTRAIL_S3_POSTURE": "true",
    },
    clear=False,
)
@patch("boto3.client")
def test_cloudtrail_no_trail_finding(mock_boto_client, load_lambda):
    """When no CloudTrail exists, finding is reported."""
    mock_logs = MagicMock()
    mock_logs.get_paginator.return_value = _make_paginator([{"logGroups": []}])
    mock_ct = MagicMock()
    mock_ct.describe_trails.return_value = {"trailList": []}

    def client(svc, **_kw):
        if svc == "logs":
            return mock_logs
        if svc == "cloudtrail":
            return mock_ct
        return MagicMock()

    mock_boto_client.side_effect = client

    mod = load_lambda("log-janitor")
    result = mod.lambda_handler({}, None)

    assert result["findings"]["cloudtrail"]
    assert any(f.get("issue") == "no_trail" for f in result["findings"]["cloudtrail"])


@patch.dict(
    "os.environ",
    {
        "MODE": "AUDIT",
        "REGIONS": "us-east-2",
        "SNS_TOPIC_ARN": "arn:aws:sns:us-east-2:123:janitor",
        "REPORT_ONLY_ON_DRIFT": "true",
        "ENABLE_ALARMS": "false",
        "ENABLE_DASHBOARD": "false",
        "ENABLE_CLOUDTRAIL_TRIPWIRES": "false",
        "ENABLE_CLOUDTRAIL_S3_POSTURE": "true",
    },
    clear=False,
)
@patch("boto3.client")
def test_sns_publish_when_drift(mock_boto_client, load_lambda):
    """When SNS_TOPIC_ARN set and drift present, Publish is called."""
    mock_logs = MagicMock()
    mock_logs.get_paginator.return_value = _make_paginator([
        {"logGroups": [{"logGroupName": "/aws/lambda/x", "retentionInDays": None}]}
    ])
    mock_ct = MagicMock()
    mock_ct.describe_trails.return_value = {"trailList": [{"Name": "t1", "IsMultiRegionTrail": True, "S3BucketName": "b1", "LogFileValidationEnabled": True}]}
    mock_ct.get_trail_status.return_value = {"IsLogging": True}
    mock_s3 = MagicMock()
    mock_s3.get_public_access_block.return_value = {
        "PublicAccessBlockConfiguration": {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        }
    }
    mock_s3.get_bucket_versioning.return_value = {"Status": "Enabled"}
    mock_s3.get_bucket_encryption.return_value = {
        "ServerSideEncryptionConfiguration": {"Rules": [{}]}
    }
    mock_s3.get_bucket_lifecycle_configuration.return_value = {"Rules": [{}]}
    mock_sns = MagicMock()

    def client(svc, **_kw):
        if svc == "logs":
            return mock_logs
        if svc == "cloudtrail":
            return mock_ct
        if svc == "s3":
            return mock_s3
        if svc == "sns":
            return mock_sns
        return MagicMock()

    mock_boto_client.side_effect = client

    mod = load_lambda("log-janitor")
    result = mod.lambda_handler({}, None)

    assert result["findings"]["log_groups"]["drifted"] == 1
    mock_sns.publish.assert_called_once()
    call = mock_sns.publish.call_args
    assert call.kwargs["TopicArn"] == "arn:aws:sns:us-east-2:123:janitor"
    assert "Janitor" in call.kwargs["Subject"]


@patch.dict(
    "os.environ",
    {
        "MODE": "AUDIT",
        "REGIONS": "us-east-2",
        "LOG_GROUP_INCLUDE_PREFIXES": "/aws/lambda/",
        "LOG_GROUP_EXCLUDE_PATTERNS": "dev,test",
        "ENABLE_ALARMS": "false",
        "ENABLE_DASHBOARD": "false",
        "ENABLE_CLOUDTRAIL_TRIPWIRES": "false",
        "ENABLE_CLOUDTRAIL_S3_POSTURE": "true",
    },
    clear=False,
)
@patch("boto3.client")
def test_excluded_log_group_not_in_scope(mock_boto_client, load_lambda):
    """Log groups matching exclude pattern are not in scope."""
    mock_logs = MagicMock()
    mock_logs.get_paginator.return_value = _make_paginator([
        {
            "logGroups": [
                {"logGroupName": "/aws/lambda/my-dev-fn", "retentionInDays": None},
                {"logGroupName": "/aws/lambda/prod-fn", "retentionInDays": None},
            ]
        }
    ])
    mock_ct = MagicMock()
    mock_ct.describe_trails.return_value = {"trailList": [{"Name": "t1", "IsMultiRegionTrail": True, "S3BucketName": "b1", "LogFileValidationEnabled": True}]}
    mock_ct.get_trail_status.return_value = {"IsLogging": True}
    mock_s3 = MagicMock()
    mock_s3.get_public_access_block.return_value = {
        "PublicAccessBlockConfiguration": {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        }
    }
    mock_s3.get_bucket_versioning.return_value = {"Status": "Enabled"}
    mock_s3.get_bucket_encryption.return_value = {
        "ServerSideEncryptionConfiguration": {"Rules": [{}]}
    }
    mock_s3.get_bucket_lifecycle_configuration.return_value = {"Rules": [{}]}

    def client(svc, **_kw):
        if svc == "logs":
            return mock_logs
        if svc == "cloudtrail":
            return mock_ct
        if svc == "s3":
            return mock_s3
        return MagicMock()

    mock_boto_client.side_effect = client

    mod = load_lambda("log-janitor")
    result = mod.lambda_handler({}, None)

    assert result["findings"]["log_groups"]["scanned"] == 2
    assert result["findings"]["log_groups"]["in_scope"] == 1
    assert result["findings"]["log_groups"]["drifted"] == 1


@patch.dict(
    "os.environ",
    {
        "MODE": "APPLY",
        "REGIONS": "us-east-2",
        "LOG_GROUP_INCLUDE_PREFIXES": "/aws/lambda/",
        "LOG_GROUP_EXCLUDE_PATTERNS": "",
        "ENABLE_ALARMS": "false",
        "ENABLE_DASHBOARD": "true",
        "ENABLE_CLOUDTRAIL_TRIPWIRES": "false",
        "ENABLE_CLOUDTRAIL_S3_POSTURE": "false",
    },
    clear=False,
)
@patch("boto3.client")
def test_dashboard_error_logged_at_error_level(mock_boto_client, load_lambda):
    """When dashboard put_dashboard fails, logger.error is called and error is in result."""
    mock_logs = MagicMock()
    mock_logs.get_paginator.return_value = _make_paginator([{"logGroups": []}])
    mock_lam = MagicMock()
    mock_lam.list_functions.return_value = {"Functions": []}
    mock_ddb = MagicMock()
    mock_ddb.get_paginator.return_value = _make_paginator([{"TableNames": []}])
    mock_sns = MagicMock()
    mock_sns.get_paginator.return_value = _make_paginator([{"Topics": []}])
    mock_cw = MagicMock()
    mock_cw.put_dashboard.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Denied"}}, "PutDashboard"
    )

    def client(svc, **_kw):
        if svc == "logs":
            return mock_logs
        if svc == "lambda":
            return mock_lam
        if svc == "dynamodb":
            return mock_ddb
        if svc == "sns":
            return mock_sns
        if svc == "cloudwatch":
            return mock_cw
        return MagicMock()

    mock_boto_client.side_effect = client

    mod = load_lambda("log-janitor")
    with patch.object(mod.logger, "error") as mock_log_error:
        result = mod.lambda_handler({}, None)

    assert len(result["errors"]) == 1
    assert result["errors"][0]["stage"] == "dashboard"
    assert result["errors"][0]["error"] == "AccessDenied: Denied"
    mock_log_error.assert_any_call("dashboard error: %s: %s", "AccessDenied", "Denied")


@patch.dict(
    "os.environ",
    {
        "MODE": "APPLY",
        "REGIONS": "us-east-2",
        "LOG_GROUP_INCLUDE_PREFIXES": "/aws/lambda/",
        "LOG_GROUP_EXCLUDE_PATTERNS": "",
        "ENABLE_ALARMS": "false",
        "ENABLE_DASHBOARD": "true",
        "ENABLE_CLOUDTRAIL_TRIPWIRES": "false",
        "ENABLE_CLOUDTRAIL_S3_POSTURE": "false",
    },
    clear=False,
)
@patch("boto3.client")
def test_dashboard_filters_empty_lambda_and_table_names(mock_boto_client, load_lambda):
    """Empty or whitespace-only names are excluded so PutDashboard does not get invalid dimensions."""
    mock_logs = MagicMock()
    mock_logs.get_paginator.return_value = _make_paginator([{"logGroups": []}])
    mock_lam = MagicMock()
    mock_lam.list_functions.return_value = {
        "Functions": [
            {"FunctionName": ""},
            {"FunctionName": "  "},
            {"FunctionName": "suigetsukan-log-janitor"},
        ]
    }
    mock_ddb = MagicMock()
    mock_ddb.get_paginator.return_value = _make_paginator([{"TableNames": ["", "  ", "mother-hen-devices"]}])
    mock_sns = MagicMock()
    mock_sns.get_paginator.return_value = _make_paginator([{"Topics": []}])
    mock_cw = MagicMock()

    def client(svc, **_kw):
        if svc == "logs":
            return mock_logs
        if svc == "lambda":
            return mock_lam
        if svc == "dynamodb":
            return mock_ddb
        if svc == "sns":
            return mock_sns
        if svc == "cloudwatch":
            return mock_cw
        return MagicMock()

    mock_boto_client.side_effect = client

    mod = load_lambda("log-janitor")
    mod.lambda_handler({}, None)

    mock_cw.put_dashboard.assert_called_once()
    call_kw = mock_cw.put_dashboard.call_args[1]
    body = json.loads(call_kw["DashboardBody"])
    for w in body.get("widgets", []):
        metrics = w.get("properties", {}).get("metrics", [])
        for m in metrics:
            if isinstance(m, list):
                for val in m:
                    if isinstance(val, str) and val.strip() == "":
                        assert False, "Dashboard body must not contain empty dimension values"
    assert call_kw["DashboardName"] == "MotherHen-Ops"


@patch.dict(
    "os.environ",
    {
        "MODE": "APPLY",
        "REGIONS": "us-east-2",
        "LOG_GROUP_INCLUDE_PREFIXES": "/aws/lambda/",
        "LOG_GROUP_EXCLUDE_PATTERNS": "",
        "ENABLE_ALARMS": "false",
        "ENABLE_DASHBOARD": "true",
        "ENABLE_CLOUDTRAIL_TRIPWIRES": "false",
        "ENABLE_CLOUDTRAIL_S3_POSTURE": "false",
    },
    clear=False,
)
@patch("boto3.client")
def test_dashboard_invalid_parameter_error_in_result_and_log(mock_boto_client, load_lambda):
    """When put_dashboard raises InvalidParameterValueException, full code and message are in result and log."""
    mock_logs = MagicMock()
    mock_logs.get_paginator.return_value = _make_paginator([{"logGroups": []}])
    mock_lam = MagicMock()
    mock_lam.list_functions.return_value = {"Functions": []}
    mock_ddb = MagicMock()
    mock_ddb.get_paginator.return_value = _make_paginator([{"TableNames": []}])
    mock_sns = MagicMock()
    mock_sns.get_paginator.return_value = _make_paginator([{"Topics": []}])
    mock_cw = MagicMock()
    mock_cw.put_dashboard.side_effect = ClientError(
        {"Error": {"Code": "InvalidParameterValueException", "Message": "Dashboard body invalid"}},
        "PutDashboard",
    )

    def client(svc, **_kw):
        if svc == "logs":
            return mock_logs
        if svc == "lambda":
            return mock_lam
        if svc == "dynamodb":
            return mock_ddb
        if svc == "sns":
            return mock_sns
        if svc == "cloudwatch":
            return mock_cw
        return MagicMock()

    mock_boto_client.side_effect = client

    mod = load_lambda("log-janitor")
    with patch.object(mod.logger, "error") as mock_log_error:
        result = mod.lambda_handler({}, None)

    assert len(result["errors"]) == 1
    assert result["errors"][0]["stage"] == "dashboard"
    assert result["errors"][0]["error"] == "InvalidParameterValueException: Dashboard body invalid"
    mock_log_error.assert_any_call(
        "dashboard error: %s: %s", "InvalidParameterValueException", "Dashboard body invalid"
    )


@patch.dict(
    "os.environ",
    {
        "MODE": "APPLY",
        "REGIONS": "us-east-2",
        "DASHBOARD_NAME": "Bad Name!",
        "LOG_GROUP_INCLUDE_PREFIXES": "/aws/lambda/",
        "LOG_GROUP_EXCLUDE_PATTERNS": "",
        "ENABLE_ALARMS": "false",
        "ENABLE_DASHBOARD": "true",
        "ENABLE_CLOUDTRAIL_TRIPWIRES": "false",
        "ENABLE_CLOUDTRAIL_S3_POSTURE": "false",
    },
    clear=False,
)
@patch("boto3.client")
def test_dashboard_name_sanitized_before_put(mock_boto_client, load_lambda):
    """Invalid dashboard name characters are sanitized so put_dashboard is called with a valid name."""
    mock_logs = MagicMock()
    mock_logs.get_paginator.return_value = _make_paginator([{"logGroups": []}])
    mock_lam = MagicMock()
    mock_lam.list_functions.return_value = {"Functions": []}
    mock_ddb = MagicMock()
    mock_ddb.get_paginator.return_value = _make_paginator([{"TableNames": []}])
    mock_sns = MagicMock()
    mock_sns.get_paginator.return_value = _make_paginator([{"Topics": []}])
    mock_cw = MagicMock()

    def client(svc, **_kw):
        if svc == "logs":
            return mock_logs
        if svc == "lambda":
            return mock_lam
        if svc == "dynamodb":
            return mock_ddb
        if svc == "sns":
            return mock_sns
        if svc == "cloudwatch":
            return mock_cw
        return MagicMock()

    mock_boto_client.side_effect = client

    mod = load_lambda("log-janitor")
    mod.lambda_handler({}, None)

    mock_cw.put_dashboard.assert_called_once()
    assert mock_cw.put_dashboard.call_args[1]["DashboardName"] == "Bad_Name_"
