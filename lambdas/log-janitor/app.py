"""
log-janitor - Mother Hen Janitor Lambda.

Enforces logging/audit hygiene: CloudWatch Logs retention, CloudTrail presence/config,
and CloudTrail S3 bucket hardening. Supports AUDIT (report only) and APPLY (remediate).
Runs on EventBridge schedule or on-demand. Idempotent; config via env vars.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, UTC
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

try:
    import mh_config

    mh_config.load_common_config()
except ImportError:
    pass

_RETRY_CONFIG = Config(retries={"max_attempts": 10, "mode": "adaptive"})

# Allow submodule import when app is loaded by path (tests); Lambda runtime already has cwd on path
_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))
from dashboard import build_dashboard_widgets  # noqa: E402


def _parse_bool(val, default=True):
    """Parse env string to bool."""
    if val is None or val == "":
        return default
    return str(val).lower() in ("true", "1", "yes")


def _parse_int(val, default):
    """Parse env string to int."""
    if val is None or val == "":
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _parse_comma_list(env_key, default_substrings):
    """Return list of stripped non-empty strings from env, or default."""
    raw = os.environ.get(env_key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raw = default_substrings
    return [p.strip() for p in raw.split(",") if p.strip()]


def _parse_exceptions_json():
    """Parse LOG_GROUP_EXCEPTIONS_JSON from env."""
    raw = os.environ.get("LOG_GROUP_EXCEPTIONS_JSON", "{}")
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def _env(key, default=""):
    """Get env var or default, stripped."""
    return (os.environ.get(key) or default).strip()


def _env_opt(key):
    """Get env var or None if empty."""
    v = _env(key)
    return v or None


def _get_sns_topic_arn():
    """SNS topic from SNS_TOPIC_ARN or REPORT_SNS_TOPIC_ARN."""
    return _env_opt("SNS_TOPIC_ARN") or _env_opt("REPORT_SNS_TOPIC_ARN")


def _get_base_config():
    """Base config (mode, regions, retention, log group filters)."""
    include_prefixes = _parse_comma_list("LOG_GROUP_INCLUDE_PREFIXES", "/aws/lambda/,/aws/iot/")
    include_prefixes = include_prefixes if include_prefixes else ["/aws/lambda/", "/aws/iot/"]
    return {
        "mode": (_env("MODE", "APPLY") or "APPLY").upper(),
        "regions": (_env("REGIONS", "us-east-2") or "us-east-2"),
        "default_retention_days": _parse_int(_env("DEFAULT_RETENTION_DAYS"), 90),
        "high_risk_retention_days": _parse_int(_env("HIGH_RISK_RETENTION_DAYS"), 90),
        "log_group_exceptions": _parse_exceptions_json(),
        "log_group_include_prefixes": include_prefixes,
        "log_group_exclude_patterns": _parse_comma_list(
            "LOG_GROUP_EXCLUDE_PATTERNS", "dev,test,sandbox,experimental"
        ),
        "high_risk_patterns": _parse_comma_list(
            "HIGH_RISK_PATTERNS", "cognito,provision,ota,auth,signup,token"
        ),
        "require_cloudtrail": _parse_bool(_env("REQUIRE_CLOUDTRAIL"), True),
        "require_multi_region_trail": _parse_bool(_env("REQUIRE_MULTI_REGION_TRAIL"), True),
        "require_log_file_validation": _parse_bool(_env("REQUIRE_LOG_FILE_VALIDATION"), True),
        "cloudtrail_s3_bucket": _env_opt("CLOUDTRAIL_S3_BUCKET_NAME"),
        "cloudtrail_s3_prefix": _env_opt("CLOUDTRAIL_S3_PREFIX"),
        "cloudtrail_retention_years": _parse_int(_env("CLOUDTRAIL_RETENTION_YEARS"), 3),
        "require_bucket_versioning": _parse_bool(_env("REQUIRE_BUCKET_VERSIONING"), True),
        "require_bucket_encryption": _parse_bool(_env("REQUIRE_BUCKET_ENCRYPTION"), True),
        "require_block_public_access": _parse_bool(_env("REQUIRE_BLOCK_PUBLIC_ACCESS"), True),
        "require_bucket_lifecycle": _parse_bool(_env("REQUIRE_BUCKET_LIFECYCLE"), True),
        "sns_topic_arn": _get_sns_topic_arn(),
        "report_only_on_drift": _parse_bool(_env("REPORT_ONLY_ON_DRIFT"), True),
        "max_drift_items_in_message": _parse_int(_env("MAX_DRIFT_ITEMS_IN_MESSAGE"), 50),
        "allow_cloudtrail_create": _parse_bool(_env("ALLOW_CLOUDTRAIL_CREATE"), False),
    }


def _get_feature_and_alarm_config():
    """Feature flags and alarm/dashboard config."""
    return {
        "enable_retention": _parse_bool(os.environ.get("ENABLE_RETENTION"), True),
        "enable_alarms": _parse_bool(os.environ.get("ENABLE_ALARMS"), True),
        "enable_dashboard": _parse_bool(os.environ.get("ENABLE_DASHBOARD"), True),
        "enable_cloudtrail_tripwires": _parse_bool(
            os.environ.get("ENABLE_CLOUDTRAIL_TRIPWIRES"), True
        ),
        "enable_cloudtrail_s3_posture": _parse_bool(
            os.environ.get("ENABLE_CLOUDTRAIL_S3_POSTURE"), False
        ),
        "critical_lambda_prefixes": _parse_comma_list("CRITICAL_LAMBDA_PREFIXES", "suigetsukan-"),
        "critical_lambdas": _parse_comma_list("CRITICAL_LAMBDAS", ""),
        "ddb_table_prefixes": _parse_comma_list("DDB_TABLE_PREFIXES", ""),
        "ddb_tables": _parse_comma_list("DDB_TABLES", ""),
        "sns_topic_prefixes": _parse_comma_list("SNS_TOPIC_PREFIXES", ""),
        "sns_topics": _parse_comma_list("SNS_TOPICS", ""),
        "alarm_sns_topic_arn": (os.environ.get("ALARM_SNS_TOPIC_ARN") or "").strip() or None,
        "cloudtrail_log_group_name": (os.environ.get("CLOUDTRAIL_LOG_GROUP_NAME") or "").strip()
        or None,
        "cloudtrail_metric_namespace": (
            os.environ.get("CLOUDTRAIL_METRIC_NAMESPACE") or "Security/CloudTrail"
        ).strip(),
        "dashboard_name": (os.environ.get("DASHBOARD_NAME") or "MotherHen-Ops").strip(),
    }


def _get_config():
    """Load config from env with spec defaults. Returns a dict."""
    cfg = _get_base_config()
    cfg.update(_get_feature_and_alarm_config())
    return cfg


def _get_regions(config):
    """Resolve list of region names. Uses ec2 DescribeRegions when config['regions'] == 'ALL'."""
    regions_val = config["regions"]
    if regions_val.upper() == "ALL":
        try:
            ec2 = boto3.client("ec2", config=_RETRY_CONFIG)
            response = ec2.describe_regions(AllRegions=False)
            return [r["RegionName"] for r in response["Regions"]]
        except ClientError as e:
            logger.warning("DescribeRegions failed, defaulting to us-east-2: %s", e)
            return ["us-east-2"]
    return [r.strip() for r in regions_val.split(",") if r.strip()]


def _is_log_group_in_scope(name, config):
    """True if log group passes include prefixes and does not match exclude patterns."""
    prefixes = config["log_group_include_prefixes"]
    if not any(name.startswith(p) for p in prefixes):
        return False
    exclude = config["log_group_exclude_patterns"]
    return not any(pat.lower() in name.lower() for pat in exclude)


def _get_target_retention_days(log_group_name, config):
    """Return target retention days for this log group (exception, high-risk, or default)."""
    exceptions = config["log_group_exceptions"]
    if log_group_name in exceptions:
        return int(exceptions[log_group_name])
    patterns = config["high_risk_patterns"]
    if any(pat.lower() in log_group_name.lower() for pat in patterns):
        return config["high_risk_retention_days"]
    return config["default_retention_days"]


def _put_retention_with_backoff(logs_client, log_group_name, retention_days):
    """Call PutRetentionPolicy with simple backoff on throttle. Raises on other errors."""
    for attempt in range(5):
        try:
            logs_client.put_retention_policy(
                logGroupName=log_group_name, retentionInDays=retention_days
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ThrottlingException":
                time.sleep(2**attempt)
                continue
            raise
    return False


def _scan_log_groups_region(logs_client, region, config, mode):
    """
    Scan one region for log group retention drift. Returns dict with keys:
    scanned, in_scope, drifted, fixed, failed, findings (list of dicts).
    """
    result = {"scanned": 0, "in_scope": 0, "drifted": 0, "fixed": 0, "failed": 0, "findings": []}
    paginator = logs_client.get_paginator("describe_log_groups")
    for page in paginator.paginate():
        for lg in page.get("logGroups", []):
            result["scanned"] += 1
            name = lg.get("logGroupName", "")
            if not _is_log_group_in_scope(name, config):
                continue
            result["in_scope"] += 1
            current = lg.get("retentionInDays")
            target = _get_target_retention_days(name, config)
            if current is None or current != target:
                result["drifted"] += 1
                finding = {
                    "log_group": name,
                    "current": current,
                    "target": target,
                    "region": region,
                }
                result["findings"].append(finding)
                logger.info(
                    "DRIFT log_group=%s region=%s current=%s target=%s",
                    name,
                    region,
                    current,
                    target,
                )
                if mode == "APPLY":
                    try:
                        _put_retention_with_backoff(logs_client, name, target)
                        result["fixed"] += 1
                        finding["action"] = "fixed"
                        logger.info(
                            "FIXED log_group=%s region=%s retention=%s", name, region, target
                        )
                    except ClientError as e:
                        result["failed"] += 1
                        finding["action"] = "failed"
                        finding["error"] = str(e)
                        logger.exception("ERROR setting retention for %s: %s", name, e)
    return result


def _run_logs_retention(config, mode, regions):
    """Run CloudWatch Logs retention scan/remediation across regions. Returns aggregated result."""
    aggregated = {
        "scanned": 0,
        "in_scope": 0,
        "drifted": 0,
        "fixed": 0,
        "failed": 0,
        "findings": [],
    }
    for region in regions:
        try:
            logs_client = boto3.client("logs", region_name=region, config=_RETRY_CONFIG)
            one = _scan_log_groups_region(logs_client, region, config, mode)
            aggregated["scanned"] += one["scanned"]
            aggregated["in_scope"] += one["in_scope"]
            aggregated["drifted"] += one["drifted"]
            aggregated["fixed"] += one["fixed"]
            aggregated["failed"] += one["failed"]
            aggregated["findings"].extend(one["findings"])
        except ClientError as e:
            logger.exception("ERROR scanning logs in %s: %s", region, e)
            aggregated["findings"].append({"region": region, "error": str(e)})
    return aggregated


def _audit_one_trail(trail, cloudtrail_client, config, required_bucket):
    """Return list of findings for a single trail."""
    findings = []
    name = trail.get("Name")
    try:
        status = cloudtrail_client.get_trail_status(Name=name)
        if not status.get("IsLogging"):
            findings.append({"category": "cloudtrail", "trail": name, "issue": "logging_disabled"})
    except ClientError:
        pass
    if not trail.get("LogFileValidationEnabled") and config["require_log_file_validation"]:
        findings.append(
            {"category": "cloudtrail", "trail": name, "issue": "log_file_validation_disabled"}
        )
    bucket = (trail.get("S3BucketName") or "").strip()
    if required_bucket and bucket != required_bucket:
        findings.append(
            {
                "category": "cloudtrail",
                "trail": name,
                "issue": "wrong_bucket",
                "expected": required_bucket,
                "actual": bucket,
            }
        )
    return findings


def _run_cloudtrail_audit(cloudtrail_client, config):
    """
    Audit CloudTrail: at least one trail, multi-region, logging, validation, S3 bucket.
    Returns list of drift findings (no remediation in v1).
    """
    findings = []
    try:
        response = cloudtrail_client.describe_trails(includeShadowTrails=False)
        trails = response.get("trailList", [])
    except ClientError as e:
        findings.append({"category": "cloudtrail", "error": str(e)})
        return findings

    if config["require_cloudtrail"] and not trails:
        findings.append(
            {"category": "cloudtrail", "issue": "no_trail", "message": "No CloudTrail found"}
        )
        return findings

    multi_region = [t for t in trails if t.get("IsMultiRegionTrail")]
    if config["require_multi_region_trail"] and not multi_region:
        findings.append(
            {
                "category": "cloudtrail",
                "issue": "no_multi_region_trail",
                "message": "No multi-region trail",
            }
        )

    required_bucket = config["cloudtrail_s3_bucket"]
    for trail in trails:
        findings.extend(_audit_one_trail(trail, cloudtrail_client, config, required_bucket))
    return findings


_PAB_CONFIG = {
    "BlockPublicAcls": True,
    "BlockPublicPolicy": True,
    "IgnorePublicAcls": True,
    "RestrictPublicBuckets": True,
}


def _s3_check_block_public_access(s3_client, bucket_name, config, mode):
    """Check/apply Block Public Access. Returns (findings, actions)."""
    findings = []
    actions = []
    try:
        pab = s3_client.get_public_access_block(Bucket=bucket_name)
        block = pab.get("PublicAccessBlockConfiguration", {})
        if not all(block.get(k, False) for k in _PAB_CONFIG):
            findings.append({"bucket": bucket_name, "issue": "block_public_access_not_full"})
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchPublicAccessBlockConfiguration":
            findings.append({"bucket": bucket_name, "issue": "block_public_access_not_set"})
        else:
            findings.append({"bucket": bucket_name, "error": str(e)})
            return findings, actions
    if findings and mode == "APPLY" and config["require_block_public_access"]:
        s3_client.put_public_access_block(
            Bucket=bucket_name, PublicAccessBlockConfiguration=_PAB_CONFIG
        )
        actions.append({"bucket": bucket_name, "action": "put_public_access_block"})
    return findings, actions


def _s3_check_versioning(s3_client, bucket_name, config, mode):
    """Check/apply versioning. Returns (findings, actions)."""
    findings = []
    actions = []
    ver = s3_client.get_bucket_versioning(Bucket=bucket_name)
    if ver.get("Status") != "Enabled" and config["require_bucket_versioning"]:
        findings.append({"bucket": bucket_name, "issue": "versioning_disabled"})
        if mode == "APPLY":
            s3_client.put_bucket_versioning(
                Bucket=bucket_name, VersioningConfiguration={"Status": "Enabled"}
            )
            actions.append({"bucket": bucket_name, "action": "put_bucket_versioning"})
    return findings, actions


def _s3_check_encryption(s3_client, bucket_name, config, mode):
    """Check/apply bucket encryption. Returns (findings, actions)."""
    findings = []
    actions = []
    enc_set = False
    try:
        enc = s3_client.get_bucket_encryption(Bucket=bucket_name)
        enc_set = bool(enc.get("ServerSideEncryptionConfiguration", {}).get("Rules"))
    except ClientError as e:
        if e.response["Error"]["Code"] != "ServerSideEncryptionConfigurationNotFoundError":
            findings.append({"bucket": bucket_name, "error": str(e)})
    if not enc_set and config["require_bucket_encryption"]:
        findings.append({"bucket": bucket_name, "issue": "encryption_not_set"})
        if mode == "APPLY":
            try:
                s3_client.put_bucket_encryption(
                    Bucket=bucket_name,
                    ServerSideEncryptionConfiguration={
                        "Rules": [
                            {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
                        ]
                    },
                )
                actions.append({"bucket": bucket_name, "action": "put_bucket_encryption"})
            except ClientError as err:
                findings.append({"bucket": bucket_name, "error": str(err)})
    return findings, actions


def _s3_check_lifecycle(s3_client, bucket_name, config, mode):
    """Check/apply lifecycle. Returns (findings, actions)."""
    findings = []
    actions = []
    try:
        lc = s3_client.get_bucket_lifecycle_configuration(Bucket=bucket_name)
        rules = lc.get("Rules", [])
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchLifecycleConfiguration":
            rules = []
        else:
            findings.append({"bucket": bucket_name, "error": str(e)})
            return findings, actions
    if config["require_bucket_lifecycle"] and not rules:
        findings.append({"bucket": bucket_name, "issue": "lifecycle_not_set"})
        if mode == "APPLY":
            s3_client.put_bucket_lifecycle_configuration(
                Bucket=bucket_name,
                LifecycleConfiguration={
                    "Rules": [
                        {
                            "ID": "CloudTrailRetention",
                            "Status": "Enabled",
                            "Filter": {"Prefix": config["cloudtrail_s3_prefix"] or ""},
                            "Expiration": {"Days": config["cloudtrail_retention_years"] * 365},
                        }
                    ]
                },
            )
            actions.append({"bucket": bucket_name, "action": "put_bucket_lifecycle"})
    return findings, actions


def _run_s3_bucket_audit(s3_client, bucket_name, config, mode):
    """
    Audit (and optionally apply) S3 bucket: Block Public Access, encryption, versioning, lifecycle.
    Returns list of findings and list of actions_taken.
    """
    if not bucket_name:
        return [], []
    findings = []
    actions_taken = []
    try:
        f1, a1 = _s3_check_block_public_access(s3_client, bucket_name, config, mode)
        findings.extend(f1)
        actions_taken.extend(a1)
        f2, a2 = _s3_check_versioning(s3_client, bucket_name, config, mode)
        findings.extend(f2)
        actions_taken.extend(a2)
        f3, a3 = _s3_check_encryption(s3_client, bucket_name, config, mode)
        findings.extend(f3)
        actions_taken.extend(a3)
        f4, a4 = _s3_check_lifecycle(s3_client, bucket_name, config, mode)
        findings.extend(f4)
        actions_taken.extend(a4)
    except ClientError as e:
        findings.append({"bucket": bucket_name, "error": str(e)})
    return findings, actions_taken


def _collect_critical_lambdas(lambda_client, config):
    """Return list of Lambda function names matching prefixes or explicit list."""
    explicit = config.get("critical_lambdas") or []
    if explicit:
        return list(set(explicit))
    prefixes = config.get("critical_lambda_prefixes") or []
    names = []
    try:
        paginator = lambda_client.get_paginator("list_functions")
        for page in paginator.paginate():
            for fn in page.get("Functions", []):
                fname = fn.get("FunctionName", "")
                if any(fname.startswith(p) for p in prefixes):
                    names.append(fname)
    except ClientError as e:
        logger.warning("list_functions failed: %s", e)
    return list(set(names))


def _ensure_alarm(cw_client, spec: dict, mode: str) -> bool:
    """Create or update one CloudWatch alarm. Returns True if created/updated, else False.
    spec: alarm_name, namespace, metric_name, dimensions, topic_arn."""
    if mode != "APPLY":
        return False
    params = {
        "AlarmName": spec["alarm_name"],
        "MetricName": spec["metric_name"],
        "Namespace": spec["namespace"],
        "Dimensions": spec["dimensions"],
        "Period": 300,
        "EvaluationPeriods": 1,
        "Threshold": 0,
        "ComparisonOperator": "GreaterThanThreshold",
        "Statistic": "Sum",
        "TreatMissingData": "notBreaching",
    }
    if spec.get("topic_arn"):
        params["AlarmActions"] = [spec["topic_arn"]]
    try:
        cw_client.put_metric_alarm(**params)
        return True
    except ClientError:
        return False


def _run_lambda_alarms(cw, lam, config, mode, out):
    """Add Lambda Errors/Throttles alarms. Mutates out."""
    lambdas_list = _collect_critical_lambdas(lam, config)
    topic = config.get("alarm_sns_topic_arn")
    for fname in lambdas_list:
        dims = [{"Name": "FunctionName", "Value": fname}]
        for metric, suffix in [("Errors", "Errors"), ("Throttles", "Throttles")]:
            out["scanned"] += 1
            aname = f"Janitor-{fname}-{suffix}"
            if _ensure_alarm(
                cw,
                {
                    "alarm_name": aname,
                    "namespace": "AWS/Lambda",
                    "metric_name": metric,
                    "dimensions": dims,
                    "topic_arn": topic,
                },
                mode,
            ):
                out["created"] += 1
                out["actions"].append({"alarm": aname, "type": "lambda", "metric": metric})
            elif mode == "APPLY":
                out["failed"] += 1


def _ensure_ddb_alarm(cw, spec: dict, mode: str, out: dict) -> None:
    """Ensure one DynamoDB alarm. Mutates out. spec: table (tname), metric, topic_arn."""
    tname = spec["table"]
    metric = spec["metric"]
    topic = spec["topic_arn"]
    out["scanned"] += 1
    dims = [{"Name": "TableName", "Value": tname}]
    aname = f"Janitor-DDB-{tname}-{metric}"
    if _ensure_alarm(
        cw,
        {
            "alarm_name": aname,
            "namespace": "AWS/DynamoDB",
            "metric_name": metric,
            "dimensions": dims,
            "topic_arn": topic,
        },
        mode,
    ):
        out["created"] += 1
        out["actions"].append({"alarm": aname, "type": "dynamodb", "metric": metric})
    elif mode == "APPLY":
        out["failed"] += 1


def _resolve_ddb_tables(ddb, config):
    """Return list of DynamoDB table names from config or list_tables + prefixes."""
    tables = list(config.get("ddb_tables") or [])
    prefixes = config.get("ddb_table_prefixes") or []
    if tables:
        return list(set(tables))
    if not prefixes:
        return []
    try:
        for page in ddb.get_paginator("list_tables").paginate():
            for t in page.get("TableNames", []):
                if any(t.startswith(p) for p in prefixes):
                    tables.append(t)
    except ClientError:
        pass
    return list(set(tables))


def _run_ddb_alarms(cw, ddb, config, mode, out):
    """Add DynamoDB ThrottledRequests/SystemErrors alarms. Mutates out."""
    tables = _resolve_ddb_tables(ddb, config)
    topic = config.get("alarm_sns_topic_arn")
    for tname in tables:
        _ensure_ddb_alarm(
            cw, {"table": tname, "metric": "ThrottledRequests", "topic_arn": topic}, mode, out
        )
        _ensure_ddb_alarm(
            cw, {"table": tname, "metric": "SystemErrors", "topic_arn": topic}, mode, out
        )


def _ensure_sns_alarm(cw, tarn, topic, mode, out):
    """Ensure one SNS alarm. Mutates out."""
    out["scanned"] += 1
    name = tarn.split(":")[-1] if ":" in tarn else tarn
    dims = [{"Name": "TopicName", "Value": name}]
    aname = f"Janitor-SNS-{name}-NotificationsFailed"
    if _ensure_alarm(
        cw,
        {
            "alarm_name": aname,
            "namespace": "AWS/SNS",
            "metric_name": "NumberOfNotificationsFailed",
            "dimensions": dims,
            "topic_arn": topic,
        },
        mode,
    ):
        out["created"] += 1
        out["actions"].append({"alarm": aname, "type": "sns"})
    elif mode == "APPLY":
        out["failed"] += 1


def _sns_topic_matches_prefixes(name, arn, prefixes):
    """True if topic name or ARN matches any prefix."""
    return any(name.startswith(p) or arn.startswith(p) for p in prefixes)


def _collect_sns_topic_arns(sns_client, config):
    """Return list of SNS topic ARNs matching config."""
    topic_arns = list(config.get("sns_topics") or [])
    prefixes = config.get("sns_topic_prefixes") or []
    if topic_arns:
        return list(set(topic_arns))
    if not prefixes:
        return []
    try:
        for page in sns_client.get_paginator("list_topics").paginate():
            for t in page.get("Topics", []):
                arn = t.get("TopicArn", "")
                name = arn.split(":")[-1] if ":" in arn else arn
                if _sns_topic_matches_prefixes(name, arn, prefixes):
                    topic_arns.append(arn)
    except ClientError:
        pass
    return list(set(topic_arns))


def _run_sns_alarms(cw, sns_client, config, mode, out):
    """Add SNS NumberOfNotificationsFailed alarms. Mutates out."""
    topic_arns = _collect_sns_topic_arns(sns_client, config)
    topic = config.get("alarm_sns_topic_arn")
    for tarn in topic_arns:
        _ensure_sns_alarm(cw, tarn, topic, mode, out)


def _run_alarms_region(region, config, mode):
    """Run alarm creation for Lambda, DynamoDB, SNS in one region."""
    out = {"scanned": 0, "created": 0, "failed": 0, "actions": []}
    cw = boto3.client("cloudwatch", region_name=region, config=_RETRY_CONFIG)
    lam = boto3.client("lambda", region_name=region, config=_RETRY_CONFIG)
    ddb = boto3.client("dynamodb", region_name=region, config=_RETRY_CONFIG)
    sns = boto3.client("sns", region_name=region, config=_RETRY_CONFIG)
    _run_lambda_alarms(cw, lam, config, mode, out)
    _run_ddb_alarms(cw, ddb, config, mode, out)
    _run_sns_alarms(cw, sns, config, mode, out)
    return out


def _run_alarms(regions, config, mode, result):
    """Run alarm creation across regions. Populate result['findings']['alarms']."""
    aggregated = {"scanned": 0, "created": 0, "failed": 0, "actions": []}
    for region in regions:
        try:
            one = _run_alarms_region(region, config, mode)
            aggregated["scanned"] += one["scanned"]
            aggregated["created"] += one["created"]
            aggregated["failed"] += one["failed"]
            aggregated["actions"].extend(one["actions"])
        except ClientError as e:
            logger.error("alarms stage error region=%s: %s", region, e)
            result["errors"].append({"stage": "alarms", "region": region, "error": str(e)})
    result["findings"]["alarms"] = aggregated
    for a in aggregated["actions"]:
        logger.info("FIXED alarm=%s type=%s", a.get("alarm"), a.get("type"))
    if aggregated["scanned"] > 0:
        logger.info(
            "alarms scanned=%d created=%d failed=%d",
            aggregated["scanned"],
            aggregated["created"],
            aggregated["failed"],
        )


def _resolve_cloudtrail_bucket(cloudtrail_client, config):
    """Get CloudTrail S3 bucket from trail or config. Returns bucket name or None."""
    if config["cloudtrail_s3_bucket"]:
        return config["cloudtrail_s3_bucket"]
    try:
        response = cloudtrail_client.describe_trails(includeShadowTrails=False)
        for trail in response.get("trailList", []):
            bucket = trail.get("S3BucketName")
            if bucket:
                return bucket
    except ClientError:
        pass
    return None


def _has_drift_or_errors(result):
    """True if result has any drift or errors worth reporting."""
    f = result.get("findings", {})
    ret = f.get("retention", f.get("log_groups", {}))
    if isinstance(ret, dict) and not ret.get("skipped") and ret.get("drifted", 0) > 0:
        return True
    if f.get("cloudtrail") or f.get("s3_bucket"):
        return True
    if f.get("alarms", {}).get("failed", 0) > 0:
        return True
    if f.get("cloudtrail_tripwires", {}).get("failed", 0) > 0:
        return True
    if f.get("dashboard", {}).get("error"):
        return True
    return bool(result.get("errors"))


def _should_send_sns(config, result):
    """True if we should send SNS (topic set and report_only_on_drift allows)."""
    if not config["sns_topic_arn"]:
        return False
    if not config["report_only_on_drift"]:
        return True
    return _has_drift_or_errors(result)


def _sns_part_retention(f):
    """Build retention line for SNS or empty string."""
    ret = f.get("retention", f.get("log_groups", {}))
    lg = ret if isinstance(ret, dict) and not ret.get("skipped") else {}
    if not lg:
        return None
    return f"Retention: scanned={lg.get('scanned', 0)} drifted={lg.get('drifted', 0)} fixed={lg.get('fixed', 0)} failed={lg.get('failed', 0)}."


def _sns_part_alarms(f):
    """Alarms line or None."""
    al = f.get("alarms", {})
    if al.get("scanned", 0) > 0:
        return f"Alarms: scanned={al.get('scanned', 0)} created={al.get('created', 0)} failed={al.get('failed', 0)}."
    return None


def _sns_part_tripwires(f):
    """Tripwires line or None."""
    tw = f.get("cloudtrail_tripwires", {})
    if tw.get("skipped"):
        return "Tripwires: skipped (not configured)."
    if tw.get("scanned", 0) > 0:
        return f"Tripwires: scanned={tw.get('scanned', 0)} created={tw.get('created', 0)} failed={tw.get('failed', 0)}."
    return None


def _sns_part_sample(f, max_items):
    """Sample drift line or None."""
    ret = f.get("retention", f.get("log_groups", {}))
    lg = ret if isinstance(ret, dict) and not ret.get("skipped") else {}
    sample = (lg.get("findings", []) or [])[:max_items]
    if sample:
        return "Sample: " + json.dumps(sample[:5])
    return None


def _build_sns_parts(result, config):
    """Build list of SNS message line strings."""
    mode = result.get("execution_metadata", {}).get("mode", "?")
    parts = [f"Janitor {mode} run completed."]
    f = result.get("findings", {})
    for line in [
        _sns_part_retention(f),
        "CloudTrail: drift detected." if f.get("cloudtrail") else None,
        "S3 bucket: drift detected." if f.get("s3_bucket") else None,
        _sns_part_alarms(f),
        _sns_part_tripwires(f),
        "Dashboard: error." if f.get("dashboard", {}).get("error") else None,
        f"Errors: {len(result['errors'])}." if result.get("errors") else None,
        _sns_part_sample(f, config["max_drift_items_in_message"]),
    ]:
        if line:
            parts.append(line)
    return parts


def _build_sns_message(result, config):
    """Build SNS message body as single string."""
    return "\n".join(_build_sns_parts(result, config))


_TRIPWIRE_DEFS = [
    {"name": "RootLogin", "pattern": '{ $.userIdentity.type = "Root" }'},
    {
        "name": "StopLoggingOrDeleteTrail",
        "pattern": '{ ($.eventName = "StopLogging") || ($.eventName = "DeleteTrail") }',
    },
    {
        "name": "IAMPolicyChange",
        "pattern": '{ ($.eventName = "AttachRolePolicy") || ($.eventName = "DetachRolePolicy") || ($.eventName = "PutRolePolicy") || ($.eventName = "DeleteRolePolicy") }',
    },
    {
        "name": "IoTPolicyChange",
        "pattern": '{ ($.eventName = "CreatePolicy") || ($.eventName = "DeletePolicy") || ($.eventName = "AttachPolicy") || ($.eventName = "DetachPolicy") }',
    },
    {"name": "DeleteLogGroup", "pattern": '{ $.eventName = "DeleteLogGroup" }'},
]


def _run_cloudtrail_tripwires(region, config, mode, result):
    """Create CloudTrail metric filters + alarms. Skip if CLOUDTRAIL_LOG_GROUP_NAME unset."""
    log_group = config.get("cloudtrail_log_group_name")
    if not log_group:
        result["findings"]["cloudtrail_tripwires"] = {
            "skipped": True,
            "reason": "CLOUDTRAIL_LOG_GROUP_NAME not set",
        }
        logger.info("SKIPPED cloudtrail_tripwires=CLOUDTRAIL_LOG_GROUP_NAME not set")
        return
    namespace = config.get("cloudtrail_metric_namespace", "Security/CloudTrail")
    topic = config.get("alarm_sns_topic_arn") or config.get("sns_topic_arn")
    cw = boto3.client("cloudwatch", region_name=region, config=_RETRY_CONFIG)
    logs = boto3.client("logs", region_name=region, config=_RETRY_CONFIG)
    out = {"scanned": 0, "created": 0, "failed": 0, "actions": []}
    for tw in _TRIPWIRE_DEFS:
        mname = f"CloudTrail-{tw['name']}"
        out["scanned"] += 1
        if mode == "APPLY":
            try:
                logs.put_metric_filter(
                    logGroupName=log_group,
                    filterName=mname,
                    filterPattern=tw["pattern"],
                    metricTransformations=[
                        {
                            "metricName": tw["name"],
                            "metricNamespace": namespace,
                            "metricValue": 1,
                        }
                    ],
                )
                dims = []
                aname = f"Janitor-{mname}"
                if _ensure_alarm(
                    cw,
                    {
                        "alarm_name": aname,
                        "namespace": namespace,
                        "metric_name": tw["name"],
                        "dimensions": dims,
                        "topic_arn": topic,
                    },
                    mode,
                ):
                    out["created"] += 1
                    out["actions"].append({"filter": mname, "alarm": aname})
            except ClientError as e:
                logger.error("cloudtrail_tripwires error filter=%s: %s", mname, e)
                out["failed"] += 1
                result["errors"].append(
                    {"stage": "cloudtrail_tripwires", "filter": mname, "error": str(e)}
                )
    result["findings"]["cloudtrail_tripwires"] = out
    for a in out.get("actions", []):
        logger.info("FIXED tripwire filter=%s alarm=%s", a.get("filter"), a.get("alarm"))
    if out["scanned"] > 0:
        logger.info(
            "cloudtrail_tripwires scanned=%d created=%d failed=%d",
            out["scanned"],
            out["created"],
            out["failed"],
        )


def _collect_tables_for_dashboard(ddb, config):
    """Return list of DynamoDB table names for dashboard."""
    tables = config.get("ddb_tables") or []
    prefixes = config.get("ddb_table_prefixes") or []
    if not tables and prefixes:
        try:
            for page in ddb.get_paginator("list_tables").paginate():
                for t in page.get("TableNames", []):
                    if any(t.startswith(p) for p in prefixes):
                        tables.append(t)
        except ClientError:
            pass
    return list(set(tables))


def _sns_arns_to_names(arns):
    """Extract topic names from ARNs."""
    return [arn.split(":")[-1] if ":" in arn else arn for arn in arns]


def _collect_sns_names_for_dashboard(sns_client, config):
    """Return list of SNS topic names for dashboard (max 6)."""
    arns = config.get("sns_topics") or []
    if not arns and config.get("sns_topic_prefixes"):
        arns = _collect_sns_topic_arns(sns_client, config)
    return _sns_arns_to_names(arns)[:6]


def _sanitize_dashboard_name(name):
    """Return dashboard name with only A-Za-z0-9_-; log warning if changed."""
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    sanitized = "".join(c if c in allowed else "_" for c in (name or ""))
    if not sanitized:
        sanitized = "MotherHen-Ops"
    if sanitized != (name or ""):
        logger.warning("dashboard name sanitized from %r to %r", name, sanitized)
    return sanitized


def _filter_valid_dashboard_names(names):
    """Exclude empty or whitespace-only names to avoid PutDashboard InvalidParameter."""
    return [n for n in names if (n and isinstance(n, str) and n.strip())]


def _run_dashboard(region, config, mode, result):
    """Create or update Ops dashboard."""
    lam = boto3.client("lambda", region_name=region, config=_RETRY_CONFIG)
    ddb = boto3.client("dynamodb", region_name=region, config=_RETRY_CONFIG)
    sns = boto3.client("sns", region_name=region, config=_RETRY_CONFIG)
    lambdas_list = _filter_valid_dashboard_names(_collect_critical_lambdas(lam, config))[:12]
    tables = _filter_valid_dashboard_names(_collect_tables_for_dashboard(ddb, config))[:6]
    topic_names = _collect_sns_names_for_dashboard(sns, config)
    widgets = build_dashboard_widgets(config, lambdas_list, tables, topic_names)
    dashboard_name = _sanitize_dashboard_name(config.get("dashboard_name", "MotherHen-Ops"))
    out = {"created": False, "error": None}
    if mode == "APPLY":
        try:
            cw = boto3.client("cloudwatch", region_name=region, config=_RETRY_CONFIG)
            cw.put_dashboard(
                DashboardName=dashboard_name, DashboardBody=json.dumps({"widgets": widgets})
            )
            out["created"] = True
            logger.info("FIXED dashboard=%s", dashboard_name)
        except ClientError as e:
            err = e.response.get("Error", {})
            code = err.get("Code", "Unknown")
            msg = err.get("Message", str(e))
            logger.error("dashboard error: %s: %s", code, msg)
            out["error"] = f"{code}: {msg}"
            result["errors"].append({"stage": "dashboard", "error": f"{code}: {msg}"})
    result["findings"]["dashboard"] = out


def _run_cloudtrail_s3_with_logging(regions, config, mode, result):
    """Run CloudTrail and S3 audits, populate result, log all findings and actions."""
    primary_region = regions[0] if regions else "us-east-2"
    logger.info("mode=%s regions=%s start", mode, regions)
    try:
        ct_client = boto3.client("cloudtrail", region_name=primary_region, config=_RETRY_CONFIG)
        ct_findings = _run_cloudtrail_audit(ct_client, config)
        result["findings"]["cloudtrail"] = ct_findings
        for f in ct_findings:
            if "error" in f:
                logger.error("DRIFT category=cloudtrail error=%s", f.get("error"))
            else:
                logger.info("DRIFT category=cloudtrail %s", f)

        bucket_name = _resolve_cloudtrail_bucket(ct_client, config)
        if not bucket_name:
            logger.info("SKIPPED s3_bucket=no CloudTrail bucket found")
        s3_client = boto3.client("s3", config=_RETRY_CONFIG)
        s3_findings, s3_actions = _run_s3_bucket_audit(s3_client, bucket_name, config, mode)
        result["findings"]["s3_bucket"] = s3_findings
        result["actions_taken"].extend(s3_actions)
        for f in s3_findings:
            if "error" in f:
                logger.error("DRIFT bucket=%s error=%s", f.get("bucket", "?"), f.get("error"))
            else:
                logger.info("DRIFT bucket=%s issue=%s", f.get("bucket", "?"), f.get("issue", f))
        for a in s3_actions:
            logger.info("FIXED bucket=%s action=%s", a.get("bucket", "?"), a.get("action", "?"))
    except ClientError as e:
        result["errors"].append({"stage": "cloudtrail_s3", "error": str(e)})
        logger.exception("CloudTrail/S3 error: %s", e)


def _publish_sns_summary(config, result):
    """If SNS_TOPIC_ARN set and (drift or errors), publish a short summary."""
    if not _should_send_sns(config, result):
        return
    try:
        sns = boto3.client("sns", config=_RETRY_CONFIG)
        sns.publish(
            TopicArn=config["sns_topic_arn"],
            Subject="Janitor summary",
            Message=_build_sns_message(result, config),
        )
    except ClientError as e:
        logger.exception("Failed to publish SNS: %s", e)


def lambda_handler(event, context):
    """
    Entrypoint. Load config, run logs retention then CloudTrail then S3 audit;
    build result; optionally publish SNS; return JSON result.
    """
    start = datetime.now(UTC).isoformat()
    config = _get_config()
    mode = config["mode"]
    if mode not in ("AUDIT", "APPLY"):
        mode = "AUDIT"
        config["mode"] = mode

    regions = _get_regions(config)
    result = {
        "execution_metadata": {"mode": mode, "regions": regions, "start": start, "end": None},
        "findings": {},
        "actions_taken": [],
        "errors": [],
        "warnings": [],
    }

    # CloudWatch Logs retention (gated by ENABLE_RETENTION)
    if config.get("enable_retention", True):
        logs_result = _run_logs_retention(config, mode, regions)
        logger.info(
            "log_groups scanned=%d in_scope=%d drifted=%d fixed=%d failed=%d",
            logs_result["scanned"],
            logs_result["in_scope"],
            logs_result["drifted"],
            logs_result["fixed"],
            logs_result["failed"],
        )
        ret_data = {
            "scanned": logs_result["scanned"],
            "in_scope": logs_result["in_scope"],
            "drifted": logs_result["drifted"],
            "fixed": logs_result["fixed"],
            "failed": logs_result["failed"],
            "findings": logs_result["findings"],
        }
        result["findings"]["retention"] = ret_data
        result["findings"]["log_groups"] = ret_data  # backward compat
    else:
        result["findings"]["retention"] = {"skipped": True}

    # CloudTrail and S3 bucket (gated by ENABLE_CLOUDTRAIL_S3_POSTURE, default off)
    if config.get("enable_cloudtrail_s3_posture", False):
        _run_cloudtrail_s3_with_logging(regions, config, mode, result)
    else:
        result["findings"]["cloudtrail"] = []
        result["findings"]["s3_bucket"] = []
        logger.info("SKIPPED cloudtrail_s3=ENABLE_CLOUDTRAIL_S3_POSTURE is false")

    # Alarms (Lambda, DynamoDB, SNS) - gated by ENABLE_ALARMS
    primary_region = regions[0] if regions else "us-east-2"
    if config.get("enable_alarms", True):
        _run_alarms(regions, config, mode, result)
    else:
        result["findings"]["alarms"] = {"skipped": True}
        logger.info("SKIPPED alarms=ENABLE_ALARMS is false")

    # CloudTrail tripwire metric filters - gated by ENABLE_CLOUDTRAIL_TRIPWIRES
    if config.get("enable_cloudtrail_tripwires", True):
        _run_cloudtrail_tripwires(primary_region, config, mode, result)
    else:
        result["findings"]["cloudtrail_tripwires"] = {
            "skipped": True,
            "reason": "ENABLE_CLOUDTRAIL_TRIPWIRES is false",
        }
        logger.info("SKIPPED cloudtrail_tripwires=ENABLE_CLOUDTRAIL_TRIPWIRES is false")

    # Dashboard - gated by ENABLE_DASHBOARD
    if config.get("enable_dashboard", True):
        _run_dashboard(primary_region, config, mode, result)
    else:
        result["findings"]["dashboard"] = {"skipped": True}
        logger.info("SKIPPED dashboard=ENABLE_DASHBOARD is false")

    result["execution_metadata"]["end"] = datetime.now(UTC).isoformat()
    logger.info(
        "complete mode=%s actions_taken=%d errors=%d",
        mode,
        len(result["actions_taken"]),
        len(result["errors"]),
    )

    if config["sns_topic_arn"]:
        _publish_sns_summary(config, result)

    return result
