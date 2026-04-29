"""
log-watcher Lambda: CloudWatch Logs alert dispatcher.

Receives CloudWatch Logs subscription events, matches error-level keywords,
dedupes and throttles, then publishes SMS alerts to the Support SNS topic.
"""

import base64
import gzip
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, UTC

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
_sns = None
_dynamodb = None

LAMBDA_NAME_LOG_WATCHER = "log-watcher"


def _log_watcher_metric(name: str, value: float, extra: dict | None = None) -> None:
    try:
        import cloudwatch_metrics as cw

        cw.put_metric(LAMBDA_NAME_LOG_WATCHER, name, value, extra_dimensions=extra)
    except Exception:  # noqa: S110, BLE001 - optional metrics; ignore if missing
        pass


DEFAULT_KEYWORDS = [
    "error",
    "fatal",
    "panic",
    "failed",
    "failure",
    "abort",
    "crash",
    "exception",
    "traceback",
    "unhandled",
    "stack trace",
    "deprecated",
    "timeout",
    "timed out",
    "oom",
    "out of memory",
    "killed",
    "signal: killed",
]
UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
LONG_HEX_PATTERN = re.compile(r"[0-9a-f]{16,}", re.IGNORECASE)


def _get_sns():
    global _sns
    if _sns is None:
        _sns = boto3.client("sns", region_name=_REGION)
    return _sns


def _get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.client("dynamodb", region_name=_REGION)
    return _dynamodb


def _parse_int(val, default: int) -> int:
    """Parse env string to int."""
    if val is None or val == "":
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _parse_keywords() -> list[str]:
    """Parse KEYWORDS_JSON or KEYWORDS_CSV from env."""
    raw = os.environ.get("KEYWORDS_JSON", "").strip()
    if raw:
        try:
            lst = json.loads(raw)
            return [str(k).strip().lower() for k in lst if k]
        except json.JSONDecodeError:
            pass
    raw = os.environ.get("KEYWORDS_CSV", "").strip()
    if raw:
        return [k.strip().lower() for k in raw.split(",") if k.strip()]
    return DEFAULT_KEYWORDS


# Default ignore pattern so enroller's routine summary line never triggers an alert.
_DEFAULT_IGNORE_PATTERNS = ["log-watcher-enroller summary"]


def _parse_ignore_patterns() -> list[str]:
    """Parse IGNORE_PATTERNS_JSON from env; always includes default patterns."""
    out = list(_DEFAULT_IGNORE_PATTERNS)
    raw = os.environ.get("IGNORE_PATTERNS_JSON", "[]").strip()
    if not raw:
        return out
    try:
        lst = json.loads(raw)
        for p in lst:
            if p and (s := str(p).strip()) and s not in out:
                out.append(s)
        return out
    except json.JSONDecodeError:
        return out


def _load_config() -> dict:
    """Load config from env."""
    return {
        "alert_topic_arn": (os.environ.get("SNS_SUPPORT_TOPIC_ARN") or "").strip(),
        "dedup_table": (
            os.environ.get("AWS_DDB_DEDUP_TABLE_NAME") or "suigetsukan-log-watcher-dedup"
        ).strip(),
        "env_label": (os.environ.get("ENV") or "prod").strip(),
        "keywords": _parse_keywords(),
        "ignore_patterns": _parse_ignore_patterns(),
        "max_sms_len": _parse_int(os.environ.get("MAX_SMS_LEN"), 450),
        "max_match_lines": _parse_int(os.environ.get("MAX_MATCH_LINES"), 3),
        "dedup_window_sec": _parse_int(os.environ.get("DEDUP_WINDOW_SECONDS"), 600),
        "throttle_max_alerts": _parse_int(os.environ.get("THROTTLE_MAX_ALERTS"), 3),
        "throttle_window_sec": _parse_int(os.environ.get("THROTTLE_WINDOW_SECONDS"), 300),
    }


def _decode_payload(encoded: str) -> dict:
    """Decode base64+gzip CloudWatch Logs subscription payload."""
    data = base64.b64decode(encoded)
    decompressed = gzip.decompress(data)
    return json.loads(decompressed.decode("utf-8"))


def _normalize_message(msg: str) -> str:
    """Normalize message for dedupe: replace volatile tokens with placeholders."""
    out = UUID_PATTERN.sub("<uuid>", msg)
    out = LONG_HEX_PATTERN.sub("<hex>", out)
    return " ".join(out.split())


def _message_matches_keywords(msg: str, keywords: list[str]) -> bool:
    """True if message contains any keyword (case-insensitive)."""
    lower_msg = msg.lower()
    return any(kw in lower_msg for kw in keywords)


def _message_ignored(msg: str, patterns: list[str]) -> bool:
    """True if message matches any ignore pattern."""
    return any(p in msg for p in patterns)


def _is_warning_only(msg: str) -> bool:
    """True if message appears to be warning-level only; do not send SMS for these."""
    lower = msg.lower()
    warning_indicators = ("warn", "warning")
    serious_indicators = (
        "error",
        "fatal",
        "panic",
        "exception",
        "crash",
        "failed",
        "failure",
        "abort",
        "oom",
        "out of memory",
        "killed",
        "traceback",
        "unhandled",
        "stack trace",
    )
    has_warn = any(w in lower for w in warning_indicators)
    has_serious = any(s in lower for s in serious_indicators)
    return has_warn and not has_serious


_INFO_DEBUG_PREFIX = re.compile(r"^\[(?:INFO|DEBUG)\]\s")
_JSON_LEVEL_FIELD = re.compile(r'"level"\s*:\s*"(?P<lvl>[A-Za-z]+)"')
_LOW_SEVERITY_LEVELS = frozenset({"info", "debug", "trace", "notice"})


def _is_info_or_debug(msg: str) -> bool:
    """True if message is info/debug-level via Lambda-runtime prefix or JSON `level` field."""
    if _INFO_DEBUG_PREFIX.match(msg):
        return True
    match = _JSON_LEVEL_FIELD.search(msg)
    return bool(match and match.group("lvl").lower() in _LOW_SEVERITY_LEVELS)


def _severity_hint(msg: str) -> str:
    """Best-effort severity label from message content (for alert text only)."""
    lower_msg = msg.lower()
    if any(k in lower_msg for k in ["error", "fatal", "panic", "exception", "crash"]):
        return "ERROR"
    return "ALERT"


def _dedupe_key(log_group: str, normalized_msg: str, time_bucket: int) -> str:
    """Compute dedupe key for DynamoDB."""
    content = f"{log_group}:{normalized_msg}:{time_bucket}"
    return hashlib.sha256(content.encode()).hexdigest()


def _check_dedupe(ddb, table: str, key: str, _expires_at: int) -> bool:
    """Return True if already seen (should skip)."""
    try:
        resp = ddb.get_item(
            TableName=table,
            Key={"pk": {"S": f"dedupe:{key}"}, "sk": {"S": "0"}},
        )
        return "Item" in resp
    except ClientError:
        return False


def _record_dedupe(ddb, table: str, key: str, expires_at: int) -> None:
    """Record dedupe entry."""
    try:
        ddb.put_item(
            TableName=table,
            Item={
                "pk": {"S": f"dedupe:{key}"},
                "sk": {"S": "0"},
                "expires_at": {"N": str(expires_at)},
            },
        )
    except ClientError as e:
        logger.warning("DynamoDB put dedupe failed: %s", e)


def _get_throttle_count(ddb, table: str, log_group: str, window_start: int) -> int:
    """Return current throttle count for log group in window."""
    pk = f"throttle:{log_group}"
    sk = str(window_start)
    try:
        resp = ddb.get_item(
            TableName=table,
            Key={"pk": {"S": pk}, "sk": {"S": sk}},
        )
        item = resp.get("Item")
        if not item:
            return 0
        return int(item.get("count", {}).get("N", "0"))
    except (ClientError, ValueError):
        return 0


def _increment_throttle(
    ddb, table: str, log_group: str, window_start: int, throttle_window_sec: int
) -> None:
    """Increment throttle counter and set TTL."""
    pk = f"throttle:{log_group}"
    sk = str(window_start)
    expires_at = int(time.time()) + throttle_window_sec + 3600
    try:
        ddb.update_item(
            TableName=table,
            Key={"pk": {"S": pk}, "sk": {"S": sk}},
            UpdateExpression="SET #c = if_not_exists(#c, :zero) + :one, expires_at = :exp",
            ExpressionAttributeNames={"#c": "count"},
            ExpressionAttributeValues={
                ":zero": {"N": "0"},
                ":one": {"N": "1"},
                ":exp": {"N": str(expires_at)},
            },
        )
    except ClientError as e:
        logger.warning("DynamoDB update throttle failed: %s", e)


def _build_sms_body(config: dict, log_group: str, log_stream: str, matches: list[dict]) -> str:
    """Build SMS message body, truncated to max_sms_len."""
    env_label = config["env_label"]
    first = matches[0] if matches else {}
    severity = first.get("severity", "ALERT")
    first_ts = first.get("timestamp")
    time_str = (
        datetime.fromtimestamp(first_ts / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if first_ts
        else "?"
    )

    lines = [
        f"[{env_label}] CloudWatch Log Alert ({severity})",
        f"logGroup: {log_group}",
        f"logStream: {log_stream[:60]}..." if len(log_stream) > 60 else f"logStream: {log_stream}",
        f"firstMatch: {time_str}",
    ]
    max_lines = config["max_match_lines"]
    for m in matches[:max_lines]:
        raw = (m.get("message", "") or "").strip()
        snippet = raw[:120] + ("..." if len(raw) > 120 else "")
        lines.append(f"- {snippet}")

    extra = len(matches) - max_lines
    if extra > 0:
        lines.append(f"(+{extra} more matching lines)")

    body = "\n".join(lines)
    max_len = config["max_sms_len"]
    if len(body) > max_len:
        body = body[: max_len - 3] + "..."
    return body


def _send_throttle_suppressed(cfg: dict, ddb, throttle_info: dict, ctx: dict) -> None:
    """Send suppressed alert and increment throttle. throttle_info: log_group, match_count, window_start."""
    log_group = throttle_info["log_group"]
    match_count = throttle_info["match_count"]
    window_start = throttle_info["window_start"]
    suppressed_msg = (
        f"[{cfg['env_label']}] Log Alert SUPPRESSED: {log_group} "
        f"(+{match_count} more, throttle limit)"
    )
    try:
        _get_sns().publish(
            TopicArn=cfg["alert_topic_arn"],
            Message=suppressed_msg[: cfg["max_sms_len"]],
        )
        ctx["published"] += 1
    except ClientError as e:
        logger.error("SNS publish suppressed failed: %s", e)
    table = cfg["dedup_table"]
    throttle_sec = cfg["throttle_window_sec"]
    _increment_throttle(ddb, table, log_group, window_start, throttle_sec)


def _collect_matches(log_events: list, log_group: str, opts: dict) -> tuple[list, int, int]:
    """Collect matching events, apply ignore/dedupe. Return (matches, deduped, ignored)."""
    keywords = opts["keywords"]
    ignore_patterns = opts["ignore_patterns"]
    ddb = opts["ddb"]
    table = opts["table"]
    time_bucket = opts["time_bucket"]
    now = opts["now"]
    dedup_window = opts["dedup_window"]
    matches = []
    deduped_count = 0
    ignored_count = 0

    for evt in log_events:
        msg = evt.get("message", "")
        if not msg:
            continue
        if _message_ignored(msg, ignore_patterns):
            ignored_count += 1
            continue
        if _is_info_or_debug(msg):
            ignored_count += 1
            continue
        if not _message_matches_keywords(msg, keywords):
            continue
        if _is_warning_only(msg):
            ignored_count += 1
            continue

        norm = _normalize_message(msg)
        dkey = _dedupe_key(log_group, norm, time_bucket)
        expires_at = now + dedup_window

        if _check_dedupe(ddb, table, dkey, expires_at):
            deduped_count += 1
            continue

        matches.append(
            {
                "id": evt.get("id"),
                "timestamp": evt.get("timestamp"),
                "message": msg,
                "severity": _severity_hint(msg),
            }
        )
        _record_dedupe(ddb, table, dkey, expires_at)

    return matches, deduped_count, ignored_count


def _process_batch(payload: dict, config: dict, ctx: dict) -> None:
    """Process one CloudWatch Logs batch; update ctx."""
    log_group = payload.get("logGroup", "?")
    log_stream = payload.get("logStream", "?")
    log_events = payload.get("logEvents", [])
    cfg = config
    table = cfg["dedup_table"]
    ddb = _get_dynamodb()

    now = int(time.time())
    time_bucket = (now // 60) * 60
    throttle_window = cfg["throttle_window_sec"]
    window_start = (now // throttle_window) * throttle_window

    opts = {
        "keywords": cfg["keywords"],
        "ignore_patterns": cfg["ignore_patterns"],
        "ddb": ddb,
        "table": table,
        "time_bucket": time_bucket,
        "now": now,
        "dedup_window": cfg["dedup_window_sec"],
    }
    matches, deduped_count, ignored_count = _collect_matches(log_events, log_group, opts)

    ctx["matches_found"] += len(matches)
    ctx["deduped"] += deduped_count
    ctx["ignored"] += ignored_count

    if not matches:
        return

    throttle_max = cfg["throttle_max_alerts"]
    current = _get_throttle_count(ddb, table, log_group, window_start)

    if current >= throttle_max + 1:
        ctx["throttled"] += 1
        return

    if current == throttle_max:
        ctx["throttled"] += 1
        _send_throttle_suppressed(
            cfg,
            ddb,
            {"log_group": log_group, "match_count": len(matches), "window_start": window_start},
            ctx,
        )
        return

    body = _build_sms_body(cfg, log_group, log_stream, matches)
    try:
        _get_sns().publish(TopicArn=cfg["alert_topic_arn"], Message=body)
        ctx["published"] += 1
        _increment_throttle(ddb, table, log_group, window_start, throttle_window)
    except ClientError as e:
        logger.error("SNS publish failed: %s", e)


def lambda_handler(event, context):
    """
    Handle CloudWatch Logs subscription filter events.

    Event format: {"awslogs": {"data": "<base64+gzip>"}}
    """
    inv_id = getattr(context, "aws_request_id", "?")
    if not isinstance(inv_id, str):
        inv_id = "?"
    config = _load_config()

    if not config["alert_topic_arn"]:
        raise ValueError("SNS_SUPPORT_TOPIC_ARN environment variable is required")

    if "awslogs" not in event or "data" not in event["awslogs"]:
        logger.warning("Invalid event format (no awslogs.data)")
        return {"status": "skipped", "reason": "invalid_event"}

    try:
        payload = _decode_payload(event["awslogs"]["data"])
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        logger.warning("Failed to decode payload: %s", e)
        return {"status": "error", "reason": "decode_failed", "error": str(e)}

    log_group = payload.get("logGroup", "?")
    log_stream = payload.get("logStream", "?")
    log_events = payload.get("logEvents", [])

    ctx = {
        "matches_found": 0,
        "deduped": 0,
        "ignored": 0,
        "throttled": 0,
        "published": 0,
    }
    _process_batch(payload, config, ctx)

    if ctx["published"] > 0:
        _log_watcher_metric("AlertsSent", ctx["published"])
    summary = {
        "invocation_id": inv_id,
        "logGroup": log_group,
        "logStream": log_stream,
        "events_total": len(log_events),
        "matches": ctx["matches_found"],
        "ignored": ctx["ignored"],
        "deduped": ctx["deduped"],
        "throttled": ctx["throttled"],
        "sms_published": ctx["published"],
    }
    logger.info("log-watcher summary: %s", json.dumps(summary))
    return summary
