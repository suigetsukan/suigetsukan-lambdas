"""
REST API lambda that exposes CloudWatch RUM-derived usage stats.

Read-only proxy over CloudWatch metrics (AWS/RUM namespace) and
CloudWatch Logs Insights (RUM custom-event log group) for the admin
Statistics page of the curriculum site. Mirrors the billing-rest-api /
cognito-rest-api shape: CORS-enabled GETs, optional API Gateway
authorizer gate, JSON-only responses.
"""

import contextlib
import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

from common.constants import (
    CORS_HEADERS_ALL,
    CORS_METHODS_GET_OPTIONS,
    CORS_ORIGIN_ALL,
    DEFAULT_REGION,
    HTTP_BAD_REQUEST,
    HTTP_NO_CONTENT,
    HTTP_OK,
    HTTP_UNAUTHORIZED,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# RUM application + log group defaults. Overridable via env vars so the
# same lambda can point at a staging monitor without a redeploy.
DEFAULT_RUM_APP_MONITOR = "suigetsukan-curriculum-monitor"
DEFAULT_RUM_LOG_GROUP = "/aws/vendedlogs/RUMService_suigetsukan-curriculum-monitor857757ba"
DEFAULT_RUM_REGION = DEFAULT_REGION

# RUM stores custom events with a single top-level event_type; the
# user-defined name lives in event_details.event_type.
_RUM_CUSTOM_EVENT_TYPE = "com.amazon.rum.custom_event"
_RUM_JS_ERROR_EVENT_TYPE = "com.amazon.rum.js_error_event"

# Logs Insights polling
_QUERY_POLL_INTERVAL_SECS = 1
_QUERY_TIMEOUT_SECS = 10

# In-process response cache. Warm starts reuse it; cold starts re-query.
_CACHE_TTL_SECS = 300
_CACHE: dict[tuple[str, int], tuple[float, dict]] = {}

# Lazy client cache so module import never touches the network.
_CLIENTS: dict[str, object] = {}


def _cors_origin() -> str:
    return os.environ.get("CORS_ALLOWED_ORIGIN", CORS_ORIGIN_ALL)


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin": _cors_origin(),
        "Access-Control-Allow-Headers": CORS_HEADERS_ALL,
        "Access-Control-Allow-Methods": CORS_METHODS_GET_OPTIONS,
    }


def _success_response(body: dict) -> dict:
    return {"statusCode": HTTP_OK, "headers": _cors_headers(), "body": json.dumps(body)}


def _error_response(status_code: int, message: str) -> dict:
    return {
        "statusCode": status_code,
        "headers": _cors_headers(),
        "body": json.dumps({"error": message}),
    }


def _options_response() -> dict:
    return {"statusCode": HTTP_NO_CONTENT, "headers": _cors_headers(), "body": ""}


def _require_authorizer(event: dict) -> dict | None:
    """Off by default; flip REQUIRE_AUTHORIZER=true once API Gateway auth is wired."""
    if os.environ.get("REQUIRE_AUTHORIZER", "false").lower() != "true":
        return None
    if not event.get("requestContext", {}).get("authorizer"):
        return _error_response(
            HTTP_UNAUTHORIZED,
            "Unauthorized: API Gateway must use Cognito authorizer",
        )
    return None


def _cloudwatch_client():
    if "cloudwatch" not in _CLIENTS:
        region = os.environ.get("RUM_REGION", DEFAULT_RUM_REGION)
        _CLIENTS["cloudwatch"] = boto3.client("cloudwatch", region_name=region)
    return _CLIENTS["cloudwatch"]


def _logs_client():
    if "logs" not in _CLIENTS:
        region = os.environ.get("RUM_REGION", DEFAULT_RUM_REGION)
        _CLIENTS["logs"] = boto3.client("logs", region_name=region)
    return _CLIENTS["logs"]


def _rum_app_monitor() -> str:
    return os.environ.get("RUM_APP_MONITOR_NAME", DEFAULT_RUM_APP_MONITOR)


def _rum_log_group() -> str:
    return os.environ.get("RUM_LOG_GROUP", DEFAULT_RUM_LOG_GROUP)


def _parse_days(qs: dict | None) -> int:
    """Read ?days=N (default 7, clamp 1..90)."""
    raw = (qs or {}).get("days") if qs else None
    try:
        days = int(raw) if raw else 7
    except (TypeError, ValueError):
        days = 7
    return max(1, min(90, days))


def _window(days: int) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc)  # noqa: UP017
    return end - timedelta(days=days), end


# ---------------------------------------------------------------------
# CloudWatch metrics
# ---------------------------------------------------------------------


def _get_metric_daily(metric_name: str, days: int, statistic: str) -> list[dict]:
    """Daily series for a single AWS/RUM metric over the window."""
    start, end = _window(days)
    resp = _cloudwatch_client().get_metric_statistics(
        Namespace="AWS/RUM",
        MetricName=metric_name,
        Dimensions=[{"Name": "application_name", "Value": _rum_app_monitor()}],
        StartTime=start,
        EndTime=end,
        Period=86400,
        Statistics=[statistic],
    )
    points = sorted(resp.get("Datapoints", []), key=lambda p: p["Timestamp"])
    return [
        {"date": p["Timestamp"].strftime("%Y-%m-%d"), "value": float(p[statistic])} for p in points
    ]


def _sum_series(series: list[dict]) -> float:
    return float(sum(p["value"] for p in series))


def _avg_series(series: list[dict]) -> float | None:
    if not series:
        return None
    return float(sum(p["value"] for p in series) / len(series))


# ---------------------------------------------------------------------
# Logs Insights
# ---------------------------------------------------------------------


def _row_to_dict(row: list[dict]) -> dict[str, str]:
    return {cell["field"]: cell["value"] for cell in row}


def _run_insights_query(query_string: str, days: int) -> list[dict]:
    """Submit a Logs Insights query and return result rows (may be partial)."""
    client = _logs_client()
    start, end = _window(days)
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())
    start_resp = client.start_query(
        logGroupName=_rum_log_group(),
        startTime=start_ts,
        endTime=end_ts,
        queryString=query_string,
    )
    query_id = start_resp["queryId"]
    deadline = time.monotonic() + _QUERY_TIMEOUT_SECS
    last_results: list[list[dict]] = []
    while time.monotonic() < deadline:
        result = client.get_query_results(queryId=query_id)
        last_results = result.get("results", [])
        status = result.get("status")
        if status == "Complete":
            return [_row_to_dict(r) for r in last_results]
        if status in ("Failed", "Cancelled", "Timeout"):
            logger.warning("Logs Insights query %s ended with status %s", query_id, status)
            return [_row_to_dict(r) for r in last_results]
        time.sleep(_QUERY_POLL_INTERVAL_SECS)
    logger.info(
        "Logs Insights query %s exceeded %ss, returning partial",
        query_id,
        _QUERY_TIMEOUT_SECS,
    )
    with contextlib.suppress(ClientError):
        client.stop_query(queryId=query_id)
    return [_row_to_dict(r) for r in last_results]


# ---------------------------------------------------------------------
# Endpoint handlers
# ---------------------------------------------------------------------


def _summary(days: int) -> dict:
    sessions_series = _get_metric_daily("SessionCount", days, "Sum")
    page_views_series = _get_metric_daily("PageViewCount", days, "Sum")
    avg_duration_series = _get_metric_daily("SessionDuration", days, "Average")
    pvps_series = _get_metric_daily("PageViewCountPerSession", days, "Average")
    js_errors_series = _get_metric_daily("JsErrorCount", days, "Sum")
    http_4xx_series = _get_metric_daily("Http4xxCountPerPageView", days, "Average")
    http_5xx_series = _get_metric_daily("Http5xxCountPerPageView", days, "Average")

    sessions = int(_sum_series(sessions_series))
    page_views = int(_sum_series(page_views_series))
    return {
        "data": {
            "sessions": sessions,
            "page_views": page_views,
            "avg_session_duration_seconds": _avg_series(avg_duration_series),
            "page_views_per_session": _avg_series(pvps_series),
            "js_error_count": int(_sum_series(js_errors_series)),
            "http_4xx_per_page_view": _avg_series(http_4xx_series),
            "http_5xx_per_page_view": _avg_series(http_5xx_series),
            "daily": {
                "sessions": sessions_series,
                "page_views": page_views_series,
            },
        }
    }


def _webvitals(days: int) -> dict:
    lcp = _get_metric_daily("WebVitalsLargestContentfulPaint", days, "Average")
    cls = _get_metric_daily("WebVitalsCumulativeLayoutShift", days, "Average")
    inp = _get_metric_daily("WebVitalsInteractionToNextPaint", days, "Average")
    interaction = inp if inp else _get_metric_daily("WebVitalsFirstInputDelay", days, "Average")
    return {
        "data": {
            "lcp_seconds": _avg_series(lcp),
            "interaction_ms": _avg_series(interaction),
            "cls": _avg_series(cls),
            "daily": {
                "lcp": lcp,
                "interaction": interaction,
                "cls": cls,
            },
        }
    }


def _top_pages(days: int) -> dict:
    query = (
        f'filter event_type = "{_RUM_CUSTOM_EVENT_TYPE}"\n'
        f'| filter event_details.event_type = "PageView"\n'
        f"| stats count() as views by event_details.page\n"
        f"| sort views desc\n"
        f"| limit 20"
    )
    rows = _run_insights_query(query, days)
    items = [
        {"page": r.get("event_details.page", ""), "views": _safe_int(r.get("views"))}
        for r in rows
        if r.get("event_details.page")
    ]
    return _list_payload(items)


def _top_sections(days: int) -> dict:
    query = (
        f'filter event_type = "{_RUM_CUSTOM_EVENT_TYPE}"\n'
        f'| filter event_details.event_type = "PageView"\n'
        f"| stats count() as views by event_details.section\n"
        f"| sort views desc\n"
        f"| limit 20"
    )
    rows = _run_insights_query(query, days)
    items = [
        {"section": r.get("event_details.section", ""), "views": _safe_int(r.get("views"))}
        for r in rows
        if r.get("event_details.section")
    ]
    return _list_payload(items)


_VIDEO_EVT_TO_FIELD = {
    "VideoPlay": "plays",
    "VideoComplete": "completes",
    "VideoPause": "pauses",
}


def _new_video_bucket() -> dict[str, float]:
    return {"plays": 0, "completes": 0, "pauses": 0, "_watched_sum": 0.0, "_watched_n": 0}


def _aggregate_video_row(bucket: dict[str, float], evt: str, cnt: int, avg_watched: str) -> None:
    field = _VIDEO_EVT_TO_FIELD.get(evt)
    if field is not None:
        bucket[field] += cnt
    watched = _safe_float(avg_watched)
    if watched is not None and cnt > 0:
        bucket["_watched_sum"] += watched * cnt
        bucket["_watched_n"] += cnt


def _video_row(tech: str, b: dict[str, float]) -> dict:
    avg = round(b["_watched_sum"] / b["_watched_n"], 1) if b["_watched_n"] > 0 else None
    return {
        "technique": tech,
        "plays": int(b["plays"]),
        "completes": int(b["completes"]),
        "avg_watched_seconds": avg,
    }


def _top_videos(days: int) -> dict:
    """Aggregate VideoPlay/Complete/Pause per technique into a single row each."""
    query = (
        f'filter event_type = "{_RUM_CUSTOM_EVENT_TYPE}"\n'
        f'| filter event_details.event_type in ["VideoPlay","VideoComplete","VideoPause"]\n'
        f"| stats count() as cnt, avg(event_details.watchedSeconds) as avg_watched\n"
        f"  by event_details.event_type, event_details.technique\n"
        f"| sort cnt desc\n"
        f"| limit 200"
    )
    rows = _run_insights_query(query, days)
    by_tech: dict[str, dict[str, float]] = defaultdict(_new_video_bucket)
    for r in rows:
        tech = r.get("event_details.technique") or ""
        if not tech:
            continue
        _aggregate_video_row(
            by_tech[tech],
            r.get("event_details.event_type", ""),
            _safe_int(r.get("cnt")),
            r.get("avg_watched", ""),
        )
    items: list[dict] = [_video_row(tech, b) for tech, b in by_tech.items()]
    items.sort(key=lambda x: int(x["plays"]), reverse=True)
    return _list_payload(items[:20])


def _top_errors(days: int) -> dict:
    query = (
        f'filter event_type = "{_RUM_JS_ERROR_EVENT_TYPE}"\n'
        f"| stats count() as count by event_details.message, metadata.pageId\n"
        f"| sort count desc\n"
        f"| limit 20"
    )
    rows = _run_insights_query(query, days)
    items = [
        {
            "message": r.get("event_details.message", ""),
            "page": r.get("metadata.pageId", ""),
            "count": _safe_int(r.get("count")),
        }
        for r in rows
        if r.get("event_details.message")
    ]
    return _list_payload(items)


def _devices(days: int) -> dict:
    device_query = (
        f'filter event_type = "{_RUM_CUSTOM_EVENT_TYPE}"\n'
        f'| filter event_details.event_type = "PageView"\n'
        f"| stats count() as views by metadata.deviceType\n"
        f"| sort views desc\n"
        f"| limit 20"
    )
    browser_query = (
        f'filter event_type = "{_RUM_CUSTOM_EVENT_TYPE}"\n'
        f'| filter event_details.event_type = "PageView"\n'
        f"| stats count() as views by metadata.browserName\n"
        f"| sort views desc\n"
        f"| limit 20"
    )
    devices = _label_rows(_run_insights_query(device_query, days), "metadata.deviceType", "device")
    browsers = _label_rows(
        _run_insights_query(browser_query, days), "metadata.browserName", "browser"
    )
    if not devices and not browsers:
        return {"data": {"devices": [], "browsers": []}, "note": "no events in window"}
    return {"data": {"devices": devices, "browsers": browsers}}


def _geography(days: int) -> dict:
    country_query = (
        f'filter event_type = "{_RUM_CUSTOM_EVENT_TYPE}"\n'
        f'| filter event_details.event_type = "PageView"\n'
        f"| stats count() as views by metadata.countryCode\n"
        f"| sort views desc\n"
        f"| limit 20"
    )
    region_query = (
        f'filter event_type = "{_RUM_CUSTOM_EVENT_TYPE}"\n'
        f'| filter event_details.event_type = "PageView"\n'
        f"| stats count() as views by metadata.countryCode, metadata.subdivisionCode\n"
        f"| sort views desc\n"
        f"| limit 50"
    )
    countries = _label_rows(
        _run_insights_query(country_query, days), "metadata.countryCode", "country"
    )
    region_rows = _run_insights_query(region_query, days)
    regions = [
        {
            "country": r.get("metadata.countryCode", ""),
            "region": r.get("metadata.subdivisionCode", ""),
            "views": _safe_int(r.get("views")),
        }
        for r in region_rows
        if r.get("metadata.countryCode")
    ]
    if not countries and not regions:
        return {"data": {"countries": [], "regions": []}, "note": "no events in window"}
    return {"data": {"countries": countries, "regions": regions}}


def _label_rows(rows: list[dict], field: str, label: str) -> list[dict]:
    return [
        {label: r.get(field, ""), "views": _safe_int(r.get("views"))} for r in rows if r.get(field)
    ]


def _list_payload(items: list[dict]) -> dict:
    if not items:
        return {"data": [], "note": "no events in window"}
    return {"data": items}


def _safe_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------


_ENDPOINTS = {
    "/usage/summary": _summary,
    "/usage/webvitals": _webvitals,
    "/usage/topPages": _top_pages,
    "/usage/topSections": _top_sections,
    "/usage/topVideos": _top_videos,
    "/usage/topErrors": _top_errors,
    "/usage/devices": _devices,
    "/usage/geography": _geography,
}


def _cached(endpoint: str, days: int, fn) -> dict:
    """Return cached body if present and unexpired; otherwise compute, cache, return."""
    key = (endpoint, days)
    now = time.monotonic()
    entry = _CACHE.get(key)
    if entry and entry[0] > now:
        cached: dict = entry[1]
        return cached
    body: dict = fn(days)
    _CACHE[key] = (now + _CACHE_TTL_SECS, body)
    return body


def _dispatch(path: str, days: int) -> dict:
    handler = _ENDPOINTS.get(path)
    if handler is None:
        return {"data": None, "error": f"Unknown endpoint: {path}"}
    try:
        return _cached(path, days, handler)
    except ClientError as err:
        logger.warning("AWS call failed for %s: %s", path, err)
        return {"data": None, "error": str(err)}
    except Exception as err:  # noqa: BLE001
        logger.exception("Unexpected failure for %s", path)
        return {"data": None, "error": str(err)}


def lambda_handler(event: dict, _context) -> dict:
    """API Gateway proxy handler for /usage/* GET endpoints."""
    method = event.get("httpMethod")
    path = event.get("path", "")
    if method == "OPTIONS":
        return _options_response()
    auth_err = _require_authorizer(event)
    if auth_err:
        return auth_err
    if method != "GET":
        return _error_response(HTTP_BAD_REQUEST, f"Unsupported method: {method}")
    days = _parse_days(event.get("queryStringParameters"))
    body = _dispatch(path, days)
    body["days"] = days
    return _success_response(body)
