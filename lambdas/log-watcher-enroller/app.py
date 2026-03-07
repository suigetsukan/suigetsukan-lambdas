"""
log-watcher-enroller Lambda: Ensure log groups have log-watcher subscription filters.

Scheduled (e.g. hourly) to attach subscription filters to new/existing log groups
and repair drift. Uses allowlist prefixes and denylist patterns from env.
"""

import hashlib
import json
import logging
import os
import time

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

try:
    import mh_config

    mh_config.load_common_config()
except ImportError:
    pass

_REGION = os.environ.get("AWS_REGION", "us-east-2")
FILTER_NAME = "log-watcher-alert"
FILTER_PATTERN = "?ERROR ?error ?Exception ?exception ?failed ?Failed ?FATAL ?fatal"
# Delay after AddPermission so CloudWatch Logs can see the new resource policy.
PERMISSION_PROPAGATION_SEC = 2


def _parse_comma_list(env_key: str, default: list[str]) -> list[str]:
    """Parse comma-separated env var to list."""
    raw = os.environ.get(env_key, "")
    if not raw or not raw.strip():
        return default
    return [p.strip() for p in raw.split(",") if p.strip()]


def _load_config() -> dict:
    """Load config from env."""
    fn_name = (os.environ.get("LOG_WATCHER_FUNCTION_NAME") or "suigetsukan-log-watcher").strip()
    prefixes = _parse_comma_list("LOG_GROUP_INCLUDE_PREFIXES", ["/aws/lambda/", "/aws/apigateway/"])
    exclude = _parse_comma_list(
        "LOG_GROUP_EXCLUDE_PATTERNS", ["dev", "test", "sandbox", "experimental"]
    )
    return {
        "function_name": fn_name,
        "include_prefixes": prefixes,
        "exclude_patterns": exclude,
    }


def _get_function_arn(lambda_client, function_name: str) -> str:
    """Resolve Lambda function ARN by name."""
    resp = lambda_client.get_function(FunctionName=function_name)
    return resp["Configuration"]["FunctionArn"]


def _get_account_id(sts_client) -> str:
    """Get current account ID."""
    return sts_client.get_caller_identity()["Account"]


def _matches_prefix(name: str, prefixes: list[str]) -> bool:
    return any(name.startswith(p) for p in prefixes)


def _matches_exclude(name: str, patterns: list[str]) -> bool:
    return any(p in name for p in patterns)


def _should_skip_log_group(name: str, destination_log_group: str) -> bool:
    """True if this log group should not get a subscription filter (e.g. self or log-watcher)."""
    if name == destination_log_group:
        return True
    own = f"/aws/lambda/{os.environ.get('AWS_LAMBDA_FUNCTION_NAME', '')}"
    return bool(own and name == own)


def _add_logs_permission(lambda_client, function_arn: str, log_group_arn: str) -> bool:
    """Grant logs.amazonaws.com permission. Return True on success or if already exists."""
    h = hashlib.sha256(log_group_arn.encode()).hexdigest()[:32]
    stmt_id = f"AllowLogs-{h}"
    try:
        lambda_client.add_permission(
            FunctionName=function_arn,
            StatementId=stmt_id,
            Action="lambda:InvokeFunction",
            Principal="logs.amazonaws.com",
            SourceArn=log_group_arn,
        )
        return True
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ResourceConflictException":
            return True
        logger.warning("add_permission failed for %s: %s", log_group_arn, e)
        return False


def _has_correct_filter(logs_client, log_group_name: str, function_arn: str) -> bool:
    """Return True if the log group already has the correct subscription filter."""
    try:
        resp = logs_client.describe_subscription_filters(
            logGroupName=log_group_name,
            filterNamePrefix=FILTER_NAME,
        )
        for sf in resp.get("subscriptionFilters", []):
            if sf.get("filterName") == FILTER_NAME and sf.get("destinationArn") == function_arn:
                return True
    except ClientError:
        pass
    return False


_PUT_FILTER_MAX_RETRIES = 4
_PUT_FILTER_INITIAL_DELAY = 2


def _put_filter_with_retry(logs_client, log_group_name: str, function_arn: str) -> None:
    """Call PutSubscriptionFilter with exponential backoff on permission-propagation errors."""
    delay = _PUT_FILTER_INITIAL_DELAY
    for attempt in range(_PUT_FILTER_MAX_RETRIES):
        try:
            logs_client.put_subscription_filter(
                logGroupName=log_group_name,
                filterName=FILTER_NAME,
                filterPattern=FILTER_PATTERN,
                destinationArn=function_arn,
            )
            return
        except ClientError as e:
            is_permission_err = (
                e.response["Error"]["Code"] == "InvalidParameterException"
                and "permission" in (e.response["Error"].get("Message") or "").lower()
            )
            last_attempt = attempt == _PUT_FILTER_MAX_RETRIES - 1
            if not is_permission_err or last_attempt:
                raise
            logger.info(
                "put_subscription_filter waiting %ds for permission propagation (%s, attempt %d)",
                delay,
                log_group_name,
                attempt + 1,
            )
            time.sleep(delay)
            delay *= 2


def _enroll_log_group(
    logs_client, lambda_client, log_group_name: str, function_arn: str, base_arn: str
) -> bool:
    """Attach subscription filter to log group. Return True if enrolled or already correct."""
    if _has_correct_filter(logs_client, log_group_name, function_arn):
        return True

    log_group_arn = f"{base_arn}{log_group_name}:*"

    if not _add_logs_permission(lambda_client, function_arn, log_group_arn):
        return False

    time.sleep(PERMISSION_PROPAGATION_SEC)

    try:
        _put_filter_with_retry(logs_client, log_group_name, function_arn)
        return True
    except ClientError as e:
        logger.warning("put_subscription_filter failed for %s: %s", log_group_name, e)
        return False


def lambda_handler(event, context):  # pylint: disable=unused-argument
    """
    List log groups, attach subscription filters to those in scope.

    Returns summary with enrolled, skipped, failed counts.
    """
    config = _load_config()

    logs_client = boto3.client("logs", region_name=_REGION)
    lambda_client = boto3.client("lambda", region_name=_REGION)
    sts_client = boto3.client("sts", region_name=_REGION)

    try:
        function_arn = _get_function_arn(lambda_client, config["function_name"])
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            logger.error("log-watcher Lambda %s not found", config["function_name"])
            return {"status": "error", "reason": "log_watcher_not_found"}
        raise

    account_id = _get_account_id(sts_client)
    base_arn = f"arn:aws:logs:{_REGION}:{account_id}:log-group:"

    prefixes = config["include_prefixes"]
    exclude = config["exclude_patterns"]
    destination_log_group = f"/aws/lambda/{config['function_name']}"

    enrolled = 0
    skipped = 0
    failed = 0

    paginator = logs_client.get_paginator("describe_log_groups")
    for page in paginator.paginate():
        for lg in page.get("logGroups", []):
            name = lg["logGroupName"]
            if not _matches_prefix(name, prefixes):
                skipped += 1
                continue
            if _matches_exclude(name, exclude):
                skipped += 1
                continue
            if _should_skip_log_group(name, destination_log_group):
                skipped += 1
                continue

            if _enroll_log_group(logs_client, lambda_client, name, function_arn, base_arn):
                enrolled += 1
                logger.info("Enrolled: %s", name)
            else:
                failed += 1

    summary = {
        "enrolled": enrolled,
        "skipped": skipped,
        "failed": failed,
    }
    logger.info("log-watcher-enroller summary: %s", json.dumps(summary))
    return summary
