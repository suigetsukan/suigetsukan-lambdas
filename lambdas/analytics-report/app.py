"""
Weekly analytics report for the Suigetsukan curriculum site.

Queries AWS Pinpoint for site-utilization metrics covering the past
seven days, compares them to the prior week, and publishes a
plain-text summary to an SNS topic for email delivery.

Trigger: EventBridge schedule (weekly, Sunday evening US-Pacific).
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

from common.constants import DEFAULT_REGION

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Pinpoint KPI name templates
_EVENT_RECORD_KPI = "events.{}.record-count"
_EVENT_ENDPOINT_KPI = "events.{}.endpoint-count"
_SESSION_KPI = "sessions.count"

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


def lambda_handler(_event, _context):
    """Generate and publish the weekly analytics report."""
    pinpoint_app_id = os.environ["AWS_PINPOINT_APP_ID"]
    pinpoint_region = os.environ.get("AWS_PINPOINT_REGION", "us-west-2")
    sns_topic_arn = os.environ["AWS_SNS_ANALYTICS_TOPIC_ARN"]
    region = os.environ.get("AWS_REGION", DEFAULT_REGION)

    pinpoint = boto3.client("pinpoint", region_name=pinpoint_region)
    sns = boto3.client("sns", region_name=region)

    today = datetime.now(timezone.utc).date()  # noqa: UP017
    this_week_end = today
    this_week_start = today - timedelta(days=7)
    prev_week_end = this_week_start
    prev_week_start = prev_week_end - timedelta(days=7)

    this_week = _gather_metrics(
        pinpoint,
        pinpoint_app_id,
        this_week_start,
        this_week_end,
    )
    prev_week = _gather_metrics(
        pinpoint,
        pinpoint_app_id,
        prev_week_start,
        prev_week_end,
    )

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
#  Data collection
# -------------------------------------------------------------------


def _gather_metrics(client, app_id, start_date, end_date):
    """Query Pinpoint for all tracked KPIs over the given date range."""
    metrics = {}

    metrics["sessions"] = _query_kpi(
        client,
        app_id,
        _SESSION_KPI,
        start_date,
        end_date,
    )

    for event_name in TRACKED_EVENTS:
        kpi = _EVENT_RECORD_KPI.format(event_name)
        metrics[event_name] = _query_kpi(
            client,
            app_id,
            kpi,
            start_date,
            end_date,
        )

    metrics["unique_video_viewers"] = _query_kpi(
        client,
        app_id,
        _EVENT_ENDPOINT_KPI.format("VideoPlay"),
        start_date,
        end_date,
    )

    return metrics


def _query_kpi(client, app_id, kpi_name, start_date, end_date):
    """Return the aggregate value for a Pinpoint KPI, or None on error."""
    try:
        start_dt = datetime.combine(
            start_date,
            datetime.min.time(),
            tzinfo=timezone.utc,  # noqa: UP017
        )
        end_dt = datetime.combine(
            end_date,
            datetime.min.time(),
            tzinfo=timezone.utc,  # noqa: UP017
        )
        resp = client.get_application_date_range_kpi(
            ApplicationId=app_id,
            KpiName=kpi_name,
            StartTime=start_dt,
            EndTime=end_dt,
        )
        rows = resp.get("ApplicationDateRangeKpiResponse", {}).get("KpiResult", {}).get("Rows", [])
        return _sum_kpi_rows(rows)
    except ClientError as err:
        logger.warning(
            "KPI query failed for %s: %s",
            kpi_name,
            err.response["Error"]["Message"],
        )
        return None


def _sum_kpi_rows(rows):
    """Sum numeric values across all Pinpoint KPI result rows."""
    total = 0
    for row in rows:
        for value in row.get("Values", []):
            try:
                total += int(float(value.get("Value", "0")))
            except (ValueError, TypeError):
                continue
    return total


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
    lines.append("  (Per-art breakdown requires Pinpoint event streaming.)")
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
