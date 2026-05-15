"""
Weekly analytics report for the Suigetsukan curriculum site.

Queries CloudWatch RUM custom-event logs via CloudWatch Logs Insights
for site-utilization metrics covering the past seven days, compares
them to the prior week, and publishes a plain-text summary to an SNS
topic for email delivery.

Trigger: EventBridge schedule (weekly, Sunday evening US-Pacific).
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

from common.constants import DEFAULT_REGION

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Custom events instrumented on the curriculum site (analytics.js)
TRACKED_EVENTS = (
    "PageView",
    "SectionView",
    "VideoPlay",
    "VideoComplete",
    "VideoPause",
    "UserSignIn",
    "UserSignOut",
)

# A week-over-week change above this threshold is flagged
SIGNIFICANT_CHANGE_PCT = 20

# Logs Insights polling
_QUERY_POLL_INTERVAL_SECS = 2
_QUERY_TIMEOUT_SECS = 60

# RUM stores custom events as one log record per event with this top-level
# event_type. The user-defined event name (PageView, VideoPlay, ...) lives
# inside the event_details JSON blob.
_RUM_CUSTOM_EVENT_TYPE = "com.amazon.rum.custom_event"


def lambda_handler(_event, _context):
    """Generate and publish the weekly analytics report."""
    log_group = os.environ["RUM_LOG_GROUP_NAME"]
    rum_region = os.environ.get("RUM_LOG_REGION", DEFAULT_REGION)
    sns_topic_arn = os.environ["AWS_SNS_ANALYTICS_TOPIC_ARN"]
    region = os.environ.get("AWS_REGION", DEFAULT_REGION)

    logs = boto3.client("logs", region_name=rum_region)
    sns = boto3.client("sns", region_name=region)

    today = datetime.now(timezone.utc).date()  # noqa: UP017
    this_week_end = today
    this_week_start = today - timedelta(days=7)
    prev_week_end = this_week_start
    prev_week_start = prev_week_end - timedelta(days=7)

    this_week = _gather_metrics(logs, log_group, this_week_start, this_week_end)
    prev_week = _gather_metrics(logs, log_group, prev_week_start, prev_week_end)

    date_label = (
        f"{this_week_start.strftime('%b %d')} - "
        f"{(this_week_end - timedelta(days=1)).strftime('%b %d, %Y')}"
    )
    subject = f"Suigetsukan Weekly Analytics - {date_label}"
    body = _build_report(this_week, prev_week, date_label)

    sns.publish(
        TopicArn=sns_topic_arn,
        Subject=subject[:100],
        Message=body,
    )

    logger.info("Published analytics report for %s", date_label)
    return {"report_period": date_label, "metrics": this_week}


# -------------------------------------------------------------------
#  Data collection (CloudWatch Logs Insights against RUM log group)
# -------------------------------------------------------------------


def _gather_metrics(logs_client, log_group, start_date, end_date):
    """Aggregate RUM events for the given date range into the report dict."""
    start_ts = _to_epoch(start_date)
    end_ts = _to_epoch(end_date)

    metrics = dict.fromkeys(TRACKED_EVENTS)
    metrics["sessions"] = None
    metrics["unique_video_viewers"] = None

    event_counts = _query_event_counts(logs_client, log_group, start_ts, end_ts)
    if event_counts is not None:
        for event_name in TRACKED_EVENTS:
            metrics[event_name] = event_counts.get(event_name, 0)

    metrics["sessions"] = _query_distinct_count(
        logs_client,
        log_group,
        start_ts,
        end_ts,
        distinct_field="metadata.session_id",
    )
    metrics["unique_video_viewers"] = _query_distinct_count(
        logs_client,
        log_group,
        start_ts,
        end_ts,
        distinct_field="user_details.user_id",
        event_name_filter="VideoPlay",
    )
    return metrics


def _query_event_counts(logs_client, log_group, start_ts, end_ts):
    """Return ``{event_name: count}`` for all custom events in the window."""
    query = (
        f"fields event_details.event_type as event_name\n"
        f'| filter event_type = "{_RUM_CUSTOM_EVENT_TYPE}"\n'
        f"| stats count() as event_count by event_name\n"
        f"| limit 100"
    )
    rows = _run_query(logs_client, log_group, start_ts, end_ts, query)
    if rows is None:
        return None
    counts = {}
    for row in rows:
        cells = {cell["field"]: cell["value"] for cell in row}
        name = cells.get("event_name")
        if not name:
            continue
        counts[name] = _safe_int(cells.get("event_count"))
    return counts


def _query_distinct_count(
    logs_client,
    log_group,
    start_ts,
    end_ts,
    distinct_field,
    event_name_filter=None,
):
    """Return count_distinct(<field>) over the window, or None on error."""
    parts = [f'filter event_type = "{_RUM_CUSTOM_EVENT_TYPE}"']
    if event_name_filter is not None:
        parts.append(f'| filter event_details.event_type = "{event_name_filter}"')
    parts.append(f"| stats count_distinct({distinct_field}) as n")
    query = "\n".join(parts)

    rows = _run_query(logs_client, log_group, start_ts, end_ts, query)
    if not rows:
        return None
    cells = {cell["field"]: cell["value"] for cell in rows[0]}
    return _safe_int(cells.get("n"))


def _run_query(logs_client, log_group, start_ts, end_ts, query_string):
    """Submit a Logs Insights query and wait for results."""
    try:
        start_resp = logs_client.start_query(
            logGroupName=log_group,
            startTime=start_ts,
            endTime=end_ts,
            queryString=query_string,
        )
    except ClientError as err:
        logger.warning(
            "Logs Insights start_query failed: %s",
            err.response["Error"]["Message"],
        )
        return None

    query_id = start_resp["queryId"]
    deadline = time.monotonic() + _QUERY_TIMEOUT_SECS
    while time.monotonic() < deadline:
        try:
            result = logs_client.get_query_results(queryId=query_id)
        except ClientError as err:
            logger.warning(
                "Logs Insights get_query_results failed: %s",
                err.response["Error"]["Message"],
            )
            return None
        status = result.get("status")
        if status == "Complete":
            return result.get("results", [])
        if status in ("Failed", "Cancelled", "Timeout"):
            logger.warning("Logs Insights query ended with status %s", status)
            return None
        time.sleep(_QUERY_POLL_INTERVAL_SECS)

    logger.warning("Logs Insights query exceeded %ss timeout", _QUERY_TIMEOUT_SECS)
    try:
        logs_client.stop_query(queryId=query_id)
    except ClientError:
        logger.debug("stop_query failed for %s", query_id)
    return None


def _to_epoch(date_obj):
    """Convert a date (UTC midnight) to integer epoch seconds."""
    return int(
        datetime.combine(
            date_obj,
            datetime.min.time(),
            tzinfo=timezone.utc,  # noqa: UP017
        ).timestamp()
    )


def _safe_int(value):
    """Parse a Logs Insights cell value to int; treat junk as 0."""
    if value is None:
        return 0
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return 0


# -------------------------------------------------------------------
#  Report formatting
# -------------------------------------------------------------------


def _build_report(this_week, prev_week, date_label):
    """Build the plain-text analytics report."""
    lines = [
        "=" * 56,
        "  SUIGETSUKAN WEEKLY ANALYTICS",
        f"  {date_label}",
        "=" * 56,
        "",
    ]

    _append_highlights(lines, this_week, prev_week)
    _append_traffic(lines, this_week)
    _append_video(lines, this_week)
    _append_sections(lines, this_week)
    _append_comparison(lines, this_week, prev_week)

    lines.append("=" * 56)
    lines.append("Report generated by suigetsukan-analytics-report Lambda.")
    return "\n".join(lines)


def _append_highlights(lines, this_week, prev_week):
    """Add a summary section noting any significant changes."""
    lines.append("HIGHLIGHTS")
    lines.append("-" * 40)

    notes = []
    comparisons = [
        ("sessions", "Sessions"),
        ("PageView", "Page views"),
        ("VideoPlay", "Video plays"),
        ("UserSignIn", "Sign-ins"),
    ]
    for key, label in comparisons:
        pct = _pct_change_raw(prev_week.get(key), this_week.get(key))
        if pct is not None and abs(pct) >= SIGNIFICANT_CHANGE_PCT:
            direction = "up" if pct > 0 else "down"
            notes.append(
                f"  {label} {direction} {abs(pct):.0f}% "
                f"({_fmt(prev_week.get(key))} -> "
                f"{_fmt(this_week.get(key))})"
            )

    if notes:
        lines.extend(notes)
    else:
        lines.append("  No significant changes this week.")
    lines.append("")


def _append_traffic(lines, metrics):
    """Add the traffic overview section."""
    lines.append("TRAFFIC")
    lines.append("-" * 40)
    lines.append(f"  Sessions:    {_fmt(metrics.get('sessions'))}")
    lines.append(f"  Page Views:  {_fmt(metrics.get('PageView'))}")
    lines.append(f"  Sign-Ins:    {_fmt(metrics.get('UserSignIn'))}")
    lines.append(f"  Sign-Outs:   {_fmt(metrics.get('UserSignOut'))}")
    lines.append("")


def _append_video(lines, metrics):
    """Add the video engagement section."""
    lines.append("VIDEO ENGAGEMENT")
    lines.append("-" * 40)
    plays = metrics.get("VideoPlay")
    completions = metrics.get("VideoComplete")
    lines.append(f"  Plays:           {_fmt(plays)}")
    lines.append(f"  Completions:     {_fmt(completions)}")
    lines.append(f"  Pauses:          {_fmt(metrics.get('VideoPause'))}")
    lines.append(f"  Completion Rate: {_completion_rate(plays, completions)}")
    lines.append(f"  Unique Viewers:  {_fmt(metrics.get('unique_video_viewers'))}")
    lines.append("")


def _append_sections(lines, metrics):
    """Add the section views summary."""
    lines.append("SECTION VIEWS")
    lines.append("-" * 40)
    lines.append(f"  Total: {_fmt(metrics.get('SectionView'))}")
    lines.append("")


def _append_comparison(lines, this_week, prev_week):
    """Add the week-over-week comparison table."""
    lines.append("WEEK-OVER-WEEK COMPARISON")
    lines.append("-" * 40)
    compare_keys = [
        ("sessions", "Sessions"),
        ("PageView", "Page Views"),
        ("VideoPlay", "Video Plays"),
        ("VideoComplete", "Completions"),
        ("UserSignIn", "Sign-Ins"),
    ]
    for key, label in compare_keys:
        current = this_week.get(key)
        previous = prev_week.get(key)
        change = _fmt_pct_change(previous, current)
        lines.append(f"  {label:14s}  {_fmt(previous):>6s} -> {_fmt(current):>6s}  {change}")
    lines.append("")


# -------------------------------------------------------------------
#  Formatting helpers
# -------------------------------------------------------------------


def _pct_change_raw(previous, current):
    """Return percentage change as a float, or None if incalculable."""
    if previous is None or current is None or previous == 0:
        return None
    return ((current - previous) / previous) * 100


def _fmt_pct_change(previous, current):
    """Return a formatted percentage-change string."""
    pct = _pct_change_raw(previous, current)
    if pct is None:
        if current is not None and current > 0 and previous == 0:
            return "(new)"
        return "(n/a)"
    sign = "+" if pct >= 0 else ""
    flag = " **" if abs(pct) >= SIGNIFICANT_CHANGE_PCT else ""
    return f"({sign}{pct:.0f}%{flag})"


def _completion_rate(plays, completions):
    """Return video completion rate as a formatted string."""
    if plays is None or completions is None or plays == 0:
        return "n/a"
    rate = (completions / plays) * 100
    return f"{rate:.1f}%"


def _fmt(value):
    """Format a metric value, handling None."""
    if value is None:
        return "n/a"
    return f"{value:,}"
