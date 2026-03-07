"""Tests for log-watcher Lambda."""

import base64
import gzip
import json
from unittest.mock import MagicMock, patch


def _make_cloudwatch_event(log_group: str, log_stream: str, messages: list[dict]) -> dict:
    """Build a CloudWatch Logs subscription event (base64+gzip encoded)."""
    payload = {
        "logGroup": log_group,
        "logStream": log_stream,
        "messageType": "DATA_MESSAGE",
        "logEvents": [
            {"id": str(i), "timestamp": 1737235893000 + i, "message": m.get("message", "")}
            for i, m in enumerate(messages)
        ],
    }
    raw = json.dumps(payload).encode("utf-8")
    compressed = gzip.compress(raw)
    encoded = base64.b64encode(compressed).decode("ascii")
    return {"awslogs": {"data": encoded}}


@patch.dict(
    "os.environ",
    {
        "SNS_SUPPORT_TOPIC_ARN": "arn:aws:sns:us-east-2:123456789012:support-topic",
        "AWS_DDB_DEDUP_TABLE_NAME": "suigetsukan-log-watcher-dedup",
        "ENV": "test",
    },
    clear=False,
)
@patch("boto3.client")
def test_matching_error_publishes_sms(mock_boto_client, load_lambda):
    """When log contains error keyword, SNS publish is called."""
    mock_sns = MagicMock()
    mock_ddb = MagicMock()
    mock_ddb.get_item.return_value = {}

    def client(svc, **_kw):
        if svc == "sns":
            return mock_sns
        if svc == "dynamodb":
            return mock_ddb
        return MagicMock()

    mock_boto_client.side_effect = client

    event = _make_cloudwatch_event(
        "/aws/lambda/foo",
        "2026/02/18/[$LATEST]abc123",
        [{"message": "ERROR: Task timed out after 30 seconds"}],
    )

    mod = load_lambda("log-watcher")
    ctx = MagicMock()
    ctx.aws_request_id = "test-inv-id"
    result = mod.lambda_handler(event, ctx)

    assert result["matches"] == 1
    assert result["sms_published"] == 1
    mock_sns.publish.assert_called_once()
    call_kw = mock_sns.publish.call_args[1]
    assert "ERROR" in call_kw["Message"]
    assert "/aws/lambda/foo" in call_kw["Message"]
    assert call_kw["TopicArn"] == "arn:aws:sns:us-east-2:123456789012:support-topic"


@patch.dict(
    "os.environ",
    {
        "SNS_SUPPORT_TOPIC_ARN": "arn:aws:sns:us-east-2:123456789012:support-topic",
        "AWS_DDB_DEDUP_TABLE_NAME": "suigetsukan-log-watcher-dedup",
        "ENV": "test",
    },
    clear=False,
)
@patch("boto3.client")
def test_no_match_skips_publish(mock_boto_client, load_lambda):
    """When log has no error/warn keywords, no SNS publish."""
    mock_sns = MagicMock()
    mock_boto_client.return_value = mock_sns

    event = _make_cloudwatch_event(
        "/aws/lambda/foo",
        "stream1",
        [{"message": "INFO: Request completed successfully"}],
    )

    ctx = MagicMock()
    ctx.aws_request_id = "test-inv-id"
    mod = load_lambda("log-watcher")
    result = mod.lambda_handler(event, ctx)

    assert result["matches"] == 0
    assert result["sms_published"] == 0
    mock_sns.publish.assert_not_called()


@patch.dict(
    "os.environ",
    {
        "SNS_SUPPORT_TOPIC_ARN": "arn:aws:sns:us-east-2:123456789012:support-topic",
        "AWS_DDB_DEDUP_TABLE_NAME": "suigetsukan-log-watcher-dedup",
        "IGNORE_PATTERNS_JSON": '["deprecated dependency"]',
    },
    clear=False,
)
@patch("boto3.client")
def test_ignore_pattern_suppresses_match(mock_boto_client, load_lambda):
    """Messages matching ignore pattern are not alerted."""
    mock_sns = MagicMock()
    mock_ddb = MagicMock()
    mock_ddb.get_item.return_value = {}

    def client(svc, **_kw):
        if svc == "sns":
            return mock_sns
        if svc == "dynamodb":
            return mock_ddb
        return MagicMock()

    mock_boto_client.side_effect = client

    event = _make_cloudwatch_event(
        "/aws/lambda/foo",
        "stream1",
        [{"message": "WARN: deprecated dependency foo is no longer supported"}],
    )

    ctx = MagicMock()
    ctx.aws_request_id = "test-inv-id"
    mod = load_lambda("log-watcher")
    result = mod.lambda_handler(event, ctx)

    assert result["ignored"] == 1
    assert result["matches"] == 0
    assert result["sms_published"] == 0
    mock_sns.publish.assert_not_called()


@patch.dict(
    "os.environ",
    {
        "SNS_SUPPORT_TOPIC_ARN": "arn:aws:sns:us-east-2:123456789012:support-topic",
        "AWS_DDB_DEDUP_TABLE_NAME": "suigetsukan-log-watcher-dedup",
        "ENV": "test",
    },
    clear=False,
)
@patch("boto3.client")
def test_log_watcher_enroller_summary_ignored_by_default(mock_boto_client, load_lambda):
    """Enroller's routine summary line (contains 'failed') is ignored by default; no SNS publish."""
    mock_sns = MagicMock()
    mock_ddb = MagicMock()
    mock_ddb.get_item.return_value = {}

    def client(svc, **_kw):
        if svc == "sns":
            return mock_sns
        if svc == "dynamodb":
            return mock_ddb
        return MagicMock()

    mock_boto_client.side_effect = client

    event = _make_cloudwatch_event(
        "/aws/lambda/suigetsukan-log-watcher-enroller",
        "2026/02/20/[$LATEST]1624598e97904b1681a320a6b72b27c6",
        [
            {
                "message": "[INFO]\t2026-02-20T12:56:54.265Z\te5ba0523-1020-415e-8709-4adb81979f3c\t"
                "log-watcher-enroller summary: {\"enrolled\": 18, \"skipped\": 5, \"failed\": 0}"
            }
        ],
    )

    ctx = MagicMock()
    ctx.aws_request_id = "test-inv-id"
    mod = load_lambda("log-watcher")
    result = mod.lambda_handler(event, ctx)

    assert result["ignored"] == 1
    assert result["matches"] == 0
    assert result["sms_published"] == 0
    mock_sns.publish.assert_not_called()


@patch.dict(
    "os.environ",
    {
        "SNS_SUPPORT_TOPIC_ARN": "arn:aws:sns:us-east-2:123456789012:support-topic",
        "AWS_DDB_DEDUP_TABLE_NAME": "suigetsukan-log-watcher-dedup",
    },
    clear=False,
)
@patch("boto3.client")
def test_invalid_event_returns_skipped(mock_boto_client, load_lambda):
    """Invalid event format returns skipped status."""
    ctx = MagicMock()
    ctx.aws_request_id = "test-inv-id"
    mod = load_lambda("log-watcher")
    result = mod.lambda_handler({"invalid": "event"}, ctx)

    assert result.get("status") == "skipped"
    assert result.get("reason") == "invalid_event"


def test_normalize_message_replaces_uuid(load_lambda):
    """Normalize replaces UUID with placeholder."""
    mod = load_lambda("log-watcher")
    # pylint: disable=protected-access
    out = mod._normalize_message("Error in request abc12345-6789-1234-abcd-123456789012")
    assert "<uuid>" in out
    assert "abc12345-6789-1234-abcd-123456789012" not in out


def test_message_matches_keywords(load_lambda):
    """Keyword matching is case-insensitive."""
    mod = load_lambda("log-watcher")
    # pylint: disable=protected-access
    assert mod._message_matches_keywords("ERROR: failed", ["error", "failed"]) is True
    assert mod._message_matches_keywords("error: something", ["error"]) is True
    assert mod._message_matches_keywords("INFO: ok", ["error", "warn"]) is False


def test_is_info_or_debug(load_lambda):
    """INFO and DEBUG prefixes are detected; ERROR/WARNING are not."""
    mod = load_lambda("log-watcher")
    # pylint: disable=protected-access
    assert mod._is_info_or_debug("[INFO]\t2026-02-21T05:00:32.879Z\t...") is True
    assert mod._is_info_or_debug("[DEBUG] some debug message") is True
    assert mod._is_info_or_debug("[ERROR] something failed") is False
    assert mod._is_info_or_debug("[WARNING] deprecated API") is False
    assert mod._is_info_or_debug("plain message without prefix") is False


@patch.dict(
    "os.environ",
    {
        "SNS_SUPPORT_TOPIC_ARN": "arn:aws:sns:us-east-2:123456789012:support-topic",
        "AWS_DDB_DEDUP_TABLE_NAME": "suigetsukan-log-watcher-dedup",
        "ENV": "test",
    },
    clear=False,
)
@patch("boto3.client")
def test_info_line_with_errors_in_message_not_alerted(mock_boto_client, load_lambda):
    """[INFO] lines containing 'Errors' (e.g. alarm names) do not trigger SMS alerts."""
    mock_sns = MagicMock()
    mock_ddb = MagicMock()
    mock_ddb.get_item.return_value = {}

    def client(svc, **_kw):
        if svc == "sns":
            return mock_sns
        if svc == "dynamodb":
            return mock_ddb
        return MagicMock()

    mock_boto_client.side_effect = client

    event = _make_cloudwatch_event(
        "/aws/lambda/suigetsukan-log-janitor",
        "2026/02/21/[$LATEST]abc123",
        [
            {
                "message": "[INFO]\t2026-02-21T05:00:49.858Z\t9c871c8e-3262-4515-ab11-ac517d292f04\t"
                "FIXED alarm=Janitor-c2ds-cognito-signup-provisioner-Errors type=lambda"
            }
        ],
    )

    mod = load_lambda("log-watcher")
    ctx = MagicMock()
    ctx.aws_request_id = "test-inv-id"
    result = mod.lambda_handler(event, ctx)

    assert result["ignored"] == 1
    assert result["matches"] == 0
    assert result["sms_published"] == 0
    mock_sns.publish.assert_not_called()
