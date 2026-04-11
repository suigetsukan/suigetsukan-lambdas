"""Tests for log-watcher-enroller Lambda."""

from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError


@patch.dict(
    "os.environ",
    {
        "LOG_WATCHER_FUNCTION_NAME": "suigetsukan-log-watcher",
        "LOG_GROUP_INCLUDE_PREFIXES": "/aws/lambda/",
        "LOG_GROUP_EXCLUDE_PATTERNS": "test,dev",
    },
    clear=False,
)
@patch("boto3.client")
def test_enroller_enrolls_matching_log_groups(mock_boto_client, load_lambda):
    """Enroller attaches subscription filter to in-scope log groups."""
    mock_logs = MagicMock()

    def _paginate(**_kw):
        yield {
            "logGroups": [
                {"logGroupName": "/aws/lambda/foo"},
                {"logGroupName": "/aws/lambda/bar-prod"},
            ]
        }

    mock_logs.get_paginator.return_value.paginate.side_effect = _paginate

    mock_lambda = MagicMock()
    mock_lambda.get_function.return_value = {
        "Configuration": {
            "FunctionArn": "arn:aws:lambda:us-east-2:123:function:suigetsukan-log-watcher"
        }
    }

    mock_sts = MagicMock()
    mock_sts.get_caller_identity.return_value = {"Account": "123456789012"}

    def client(svc, **_kw):
        if svc == "logs":
            return mock_logs
        if svc == "lambda":
            return mock_lambda
        if svc == "sts":
            return mock_sts
        return MagicMock()

    mock_boto_client.side_effect = client

    mod = load_lambda("log-watcher-enroller")
    with patch.object(mod, "time", MagicMock()):
        result = mod.lambda_handler({}, MagicMock())

    assert result["enrolled"] == 2
    assert result["skipped"] == 0
    assert mock_logs.put_subscription_filter.call_count == 2


@patch.dict(
    "os.environ",
    {
        "LOG_WATCHER_FUNCTION_NAME": "suigetsukan-log-watcher",
        "LOG_GROUP_INCLUDE_PREFIXES": "/aws/lambda/",
        "LOG_GROUP_EXCLUDE_PATTERNS": "",
    },
    clear=False,
)
@patch("boto3.client")
def test_enroller_skips_destination_log_group(mock_boto_client, load_lambda):
    """Enroller skips the log-watcher's own log group (AWS disallows self-subscription)."""
    mock_logs = MagicMock()

    def _paginate(**_kw):
        yield {
            "logGroups": [
                {"logGroupName": "/aws/lambda/suigetsukan-log-watcher"},
                {"logGroupName": "/aws/lambda/other-lambda"},
            ]
        }

    mock_logs.get_paginator.return_value.paginate.side_effect = _paginate

    mock_lambda = MagicMock()
    mock_lambda.get_function.return_value = {
        "Configuration": {
            "FunctionArn": "arn:aws:lambda:us-east-2:123:function:suigetsukan-log-watcher"
        }
    }

    mock_sts = MagicMock()
    mock_sts.get_caller_identity.return_value = {"Account": "123"}

    def client(svc, **_kw):
        if svc == "logs":
            return mock_logs
        if svc == "lambda":
            return mock_lambda
        if svc == "sts":
            return mock_sts
        return MagicMock()

    mock_boto_client.side_effect = client

    mod = load_lambda("log-watcher-enroller")
    with patch.object(mod, "time", MagicMock()):
        result = mod.lambda_handler({}, MagicMock())

    assert result["enrolled"] == 1
    assert result["skipped"] == 1
    assert mock_logs.put_subscription_filter.call_count == 1
    call_args = mock_logs.put_subscription_filter.call_args
    assert call_args.kwargs["logGroupName"] == "/aws/lambda/other-lambda"


@patch.dict(
    "os.environ",
    {
        "LOG_WATCHER_FUNCTION_NAME": "suigetsukan-log-watcher",
        "LOG_GROUP_INCLUDE_PREFIXES": "/aws/lambda/",
        "LOG_GROUP_EXCLUDE_PATTERNS": "",
        "AWS_LAMBDA_FUNCTION_NAME": "suigetsukan-log-watcher-enroller",
    },
    clear=False,
)
@patch("boto3.client")
def test_enroller_skips_own_log_group(mock_boto_client, load_lambda):
    """Enroller skips its own log group so its logs are not sent to log-watcher."""
    mock_logs = MagicMock()

    def _paginate(**_kw):
        yield {
            "logGroups": [
                {"logGroupName": "/aws/lambda/suigetsukan-log-watcher-enroller"},
                {"logGroupName": "/aws/lambda/other-lambda"},
            ]
        }

    mock_logs.get_paginator.return_value.paginate.side_effect = _paginate

    mock_lambda = MagicMock()
    mock_lambda.get_function.return_value = {
        "Configuration": {
            "FunctionArn": "arn:aws:lambda:us-east-2:123:function:suigetsukan-log-watcher"
        }
    }

    mock_sts = MagicMock()
    mock_sts.get_caller_identity.return_value = {"Account": "123"}

    def client(svc, **_kw):
        if svc == "logs":
            return mock_logs
        if svc == "lambda":
            return mock_lambda
        if svc == "sts":
            return mock_sts
        return MagicMock()

    mock_boto_client.side_effect = client

    mod = load_lambda("log-watcher-enroller")
    with patch.object(mod, "time", MagicMock()):
        result = mod.lambda_handler({}, MagicMock())

    assert result["enrolled"] == 1
    assert result["skipped"] == 1
    assert mock_logs.put_subscription_filter.call_count == 1
    call_args = mock_logs.put_subscription_filter.call_args
    assert call_args.kwargs["logGroupName"] == "/aws/lambda/other-lambda"


@patch.dict(
    "os.environ",
    {
        "LOG_WATCHER_FUNCTION_NAME": "suigetsukan-log-watcher",
        "LOG_GROUP_INCLUDE_PREFIXES": "/aws/lambda/",
        "LOG_GROUP_EXCLUDE_PATTERNS": "test",
    },
    clear=False,
)
@patch("boto3.client")
def test_enroller_skips_excluded_log_groups(mock_boto_client, load_lambda):
    """Enroller skips log groups matching exclude pattern."""
    mock_logs = MagicMock()

    def _paginate(**_kw):
        yield {"logGroups": [{"logGroupName": "/aws/lambda/foo-test"}]}

    mock_logs.get_paginator.return_value.paginate.side_effect = _paginate

    mock_lambda = MagicMock()
    mock_lambda.get_function.return_value = {
        "Configuration": {
            "FunctionArn": "arn:aws:lambda:us-east-2:123:function:suigetsukan-log-watcher"
        }
    }

    mock_sts = MagicMock()
    mock_sts.get_caller_identity.return_value = {"Account": "123"}

    def client(svc, **_kw):
        if svc == "logs":
            return mock_logs
        if svc == "lambda":
            return mock_lambda
        if svc == "sts":
            return mock_sts
        return MagicMock()

    mock_boto_client.side_effect = client

    mod = load_lambda("log-watcher-enroller")
    result = mod.lambda_handler({}, MagicMock())

    assert result["enrolled"] == 0
    assert result["skipped"] == 1
    mock_logs.put_subscription_filter.assert_not_called()


@patch.dict(
    "os.environ",
    {
        "LOG_WATCHER_FUNCTION_NAME": "suigetsukan-log-watcher",
        "LOG_GROUP_INCLUDE_PREFIXES": "/aws/lambda/",
        "LOG_GROUP_EXCLUDE_PATTERNS": "",
    },
    clear=False,
)
@patch("boto3.client")
def test_enroller_treats_conflict_as_success(mock_boto_client, load_lambda):
    """When add_permission gets ResourceConflictException, enroller treats it as already-granted."""
    mock_logs = MagicMock()
    mock_logs.get_paginator.return_value.paginate.return_value = [
        {"logGroups": [{"logGroupName": "/aws/lambda/suigetsukan-billing-rest-api"}]}
    ]
    mock_logs.describe_subscription_filters.return_value = {"subscriptionFilters": []}

    mock_lambda = MagicMock()
    mock_lambda.get_function.return_value = {
        "Configuration": {
            "FunctionArn": "arn:aws:lambda:us-east-2:123:function:suigetsukan-log-watcher"
        }
    }
    mock_lambda.add_permission.side_effect = ClientError(
        {"Error": {"Code": "ResourceConflictException"}},
        "AddPermission",
    )

    mock_sts = MagicMock()
    mock_sts.get_caller_identity.return_value = {"Account": "123"}

    def client(svc, **_kw):
        if svc == "logs":
            return mock_logs
        if svc == "lambda":
            return mock_lambda
        if svc == "sts":
            return mock_sts
        return MagicMock()

    mock_boto_client.side_effect = client

    mod = load_lambda("log-watcher-enroller")
    with patch.object(mod, "time", MagicMock()):
        result = mod.lambda_handler({}, MagicMock())

    assert result["enrolled"] == 1
    mock_lambda.remove_permission.assert_not_called()
    assert mock_lambda.add_permission.call_count == 1
    call_kw = mock_logs.put_subscription_filter.call_args.kwargs
    assert call_kw["logGroupName"] == "/aws/lambda/suigetsukan-billing-rest-api"
    assert (
        call_kw["destinationArn"] == "arn:aws:lambda:us-east-2:123:function:suigetsukan-log-watcher"
    )
    assert call_kw["filterName"] == "log-watcher-alert"


@patch.dict(
    "os.environ",
    {
        "LOG_WATCHER_FUNCTION_NAME": "suigetsukan-log-watcher",
        "LOG_GROUP_INCLUDE_PREFIXES": "/aws/lambda/",
        "LOG_GROUP_EXCLUDE_PATTERNS": "",
    },
    clear=False,
)
@patch("boto3.client")
def test_enroller_skips_already_enrolled_log_group(mock_boto_client, load_lambda):
    """Enroller skips log groups that already have the correct subscription filter."""
    fn_arn = "arn:aws:lambda:us-east-2:123:function:suigetsukan-log-watcher"
    mock_logs = MagicMock()
    mock_logs.get_paginator.return_value.paginate.return_value = [
        {"logGroups": [{"logGroupName": "/aws/lambda/already-enrolled"}]}
    ]
    mock_logs.describe_subscription_filters.return_value = {
        "subscriptionFilters": [{"filterName": "log-watcher-alert", "destinationArn": fn_arn}]
    }

    mock_lambda = MagicMock()
    mock_lambda.get_function.return_value = {"Configuration": {"FunctionArn": fn_arn}}

    mock_sts = MagicMock()
    mock_sts.get_caller_identity.return_value = {"Account": "123"}

    def client(svc, **_kw):
        if svc == "logs":
            return mock_logs
        if svc == "lambda":
            return mock_lambda
        if svc == "sts":
            return mock_sts
        return MagicMock()

    mock_boto_client.side_effect = client

    mod = load_lambda("log-watcher-enroller")
    result = mod.lambda_handler({}, MagicMock())

    assert result["enrolled"] == 1
    mock_lambda.add_permission.assert_not_called()
    mock_logs.put_subscription_filter.assert_not_called()


@patch.dict(
    "os.environ",
    {
        "LOG_WATCHER_FUNCTION_NAME": "suigetsukan-log-watcher",
        "LOG_GROUP_INCLUDE_PREFIXES": "/aws/lambda/",
        "LOG_GROUP_EXCLUDE_PATTERNS": "",
    },
    clear=False,
)
@patch("boto3.client")
def test_enroller_retries_put_filter_on_permission_exception(mock_boto_client, load_lambda):
    """When PutSubscriptionFilter fails with permission message, enroller waits and retries once."""
    mock_logs = MagicMock()
    mock_logs.get_paginator.return_value.paginate.return_value = [
        {"logGroups": [{"logGroupName": "/aws/lambda/some-lambda"}]}
    ]
    mock_logs.put_subscription_filter.side_effect = [
        ClientError(
            {
                "Error": {
                    "Code": "InvalidParameterException",
                    "Message": "Could not execute the lambda function. Make sure you have given CloudWatch Logs permission to execute your function.",
                }
            },
            "PutSubscriptionFilter",
        ),
        None,
    ]

    mock_lambda = MagicMock()
    mock_lambda.get_function.return_value = {
        "Configuration": {
            "FunctionArn": "arn:aws:lambda:us-east-2:123:function:suigetsukan-log-watcher"
        }
    }

    mock_sts = MagicMock()
    mock_sts.get_caller_identity.return_value = {"Account": "123"}

    def client(svc, **_kw):
        if svc == "logs":
            return mock_logs
        if svc == "lambda":
            return mock_lambda
        if svc == "sts":
            return mock_sts
        return MagicMock()

    mock_boto_client.side_effect = client

    mod = load_lambda("log-watcher-enroller")
    with patch.object(mod, "time", MagicMock()):
        result = mod.lambda_handler({}, MagicMock())

    assert result["enrolled"] == 1
    assert mock_logs.put_subscription_filter.call_count == 2


@patch.dict(
    "os.environ",
    {"LOG_WATCHER_FUNCTION_NAME": "suigetsukan-log-watcher"},
    clear=False,
)
@patch("boto3.client")
def test_enroller_returns_error_when_log_watcher_not_found(mock_boto_client, load_lambda):
    """Enroller returns error when log-watcher Lambda does not exist."""
    mock_lambda = MagicMock()

    mock_lambda.get_function.side_effect = ClientError(
        {"Error": {"Code": "ResourceNotFoundException"}}, "GetFunction"
    )

    def client(svc, **_kw):
        if svc == "lambda":
            return mock_lambda
        return MagicMock()

    mock_boto_client.side_effect = client

    mod = load_lambda("log-watcher-enroller")
    result = mod.lambda_handler({}, MagicMock())

    assert result["status"] == "error"
    assert result["reason"] == "log_watcher_not_found"
