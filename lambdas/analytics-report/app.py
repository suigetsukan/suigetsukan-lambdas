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
from datetime import date, datetime, timedelta, timezone

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

# Daily-usage derivation. ``event_timestamp`` is the client-side epoch-ms
# timestamp on every RUM record (verified live 2026-09-08); Logs Insights
# bucketing functions (datefloor/bin) only accept @timestamp, so the ms
# field is used for span arithmetic and @timestamp for bucketing.
_EVENT_TS_FIELD = "event_timestamp"
_MS_PER_MINUTE = 60_000.0
_MIN_SESSION_MINUTES = 1.0
_CONCURRENCY_BUCKET = "5m"
_INSIGHTS_ROW_LIMIT = 10000
_INSIGHTS_TS_FORMAT = "%Y-%m-%d %H:%M:%S.%f"

# In the RUM vended log group, each custom event is one log record whose
# top-level ``event_type`` IS the user-defined event name (PageView,
# VideoPlay, ...). Session/user identity lives in ``user_details``
# (``sessionId`` / ``userId``). Verified against live records 2026-08-22.
_TRACKED_EVENTS_QUERY_LIST = ", ".join(f'"{name}"' for name in TRACKED_EVENTS)


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

    if _all_queries_failed(this_week):
        # Refuse to publish a report with no data behind it. Raising makes
        # the Lambda Errors metric fire so the failure is visible/alarmable
        # instead of silently emailing an empty report.
        msg = "All Logs Insights queries failed; aborting report publish"
        logger.error(msg)
        raise RuntimeError(msg)

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
        distinct_field="user_details.sessionId",
    )
    metrics["unique_video_viewers"] = _query_distinct_count(
        logs_client,
        log_group,
        start_ts,
        end_ts,
        distinct_field="user_details.userId",
        event_name_filter="VideoPlay",
    )
    metrics.update(_gather_daily_usage(logs_client, log_group, start_date, end_date))
    return metrics


def _all_queries_failed(metrics):
    """True when every metric is None, i.e. no query returned data."""
    return all(value is None for value in metrics.values())


def _gather_daily_usage(logs_client, log_group, start_date, end_date):
    """Return the per-day usage table plus its weekly rollups.

    Three queries per window: per-day distinct users/sessions, per-session
    first/last event span (member-minutes, active users), and distinct
    sessions per 5-minute bucket (peak concurrency). Each query failing
    independently yields ``None`` for the metrics it feeds; all three
    failing yields ``daily=None`` so ``_all_queries_failed`` still works.
    """
    start_ts = _to_epoch(start_date)
    end_ts = _to_epoch(end_date)
    day_counts = _query_daily_counts(logs_client, log_group, start_ts, end_ts)
    spans = _query_session_spans(logs_client, log_group, start_ts, end_ts)
    buckets = _query_concurrency_buckets(logs_client, log_group, start_ts, end_ts)

    daily = _build_daily_rows(start_date, end_date, day_counts, spans, buckets)
    member_minutes = _sum_daily(daily, "member_minutes")
    session_count = None if spans is None else len(spans)
    return {
        "daily": daily,
        "active_users": _distinct_users(spans),
        "member_minutes": member_minutes,
        "peak_concurrent": _max_daily(daily, "peak_concurrent"),
        "avg_session_minutes": _avg_session_minutes(member_minutes, session_count),
    }


def _query_daily_counts(logs_client, log_group, start_ts, end_ts):
    """Return ``{date: {"users": n, "sessions": n}}`` per UTC day, or None."""
    query = (
        f"filter event_type in [{_TRACKED_EVENTS_QUERY_LIST}]\n"
        f"| stats count_distinct(user_details.userId) as users,\n"
        f"        count_distinct(user_details.sessionId) as sessions\n"
        f"  by datefloor(@timestamp, 1d) as day\n"
        f"| sort day asc\n"
        f"| limit {_INSIGHTS_ROW_LIMIT}"
    )
    rows = _run_query(logs_client, log_group, start_ts, end_ts, query)
    if rows is None:
        return None
    counts = {}
    for cells in _row_dicts(rows):
        day = _parse_insights_ts(cells.get("day"))
        if day is None:
            continue
        counts[day.date()] = {
            "users": _safe_int(cells.get("users")),
            "sessions": _safe_int(cells.get("sessions")),
        }
    return counts


def _query_session_spans(logs_client, log_group, start_ts, end_ts):
    """Return ``[(user_id, first_ms, last_ms), ...]`` per session, or None."""
    query = (
        f"filter event_type in [{_TRACKED_EVENTS_QUERY_LIST}]\n"
        f"| stats min({_EVENT_TS_FIELD}) as first_ts, max({_EVENT_TS_FIELD}) as last_ts\n"
        f"  by user_details.sessionId as session_id, user_details.userId as user_id\n"
        f"| limit {_INSIGHTS_ROW_LIMIT}"
    )
    rows = _run_query(logs_client, log_group, start_ts, end_ts, query)
    if rows is None:
        return None
    spans = []
    for cells in _row_dicts(rows):
        if "first_ts" not in cells or "last_ts" not in cells:
            continue
        spans.append(
            (
                cells.get("user_id"),
                _safe_int(cells.get("first_ts")),
                _safe_int(cells.get("last_ts")),
            )
        )
    return spans


def _query_concurrency_buckets(logs_client, log_group, start_ts, end_ts):
    """Return ``[(bucket_datetime, active_sessions), ...]``, or None."""
    query = (
        f"filter event_type in [{_TRACKED_EVENTS_QUERY_LIST}]\n"
        f"| stats count_distinct(user_details.sessionId) as active\n"
        f"  by bin({_CONCURRENCY_BUCKET}) as bucket\n"
        f"| sort bucket asc\n"
        f"| limit {_INSIGHTS_ROW_LIMIT}"
    )
    rows = _run_query(logs_client, log_group, start_ts, end_ts, query)
    if rows is None:
        return None
    buckets = []
    for cells in _row_dicts(rows):
        bucket = _parse_insights_ts(cells.get("bucket"))
        if bucket is None:
            continue
        buckets.append((bucket, _safe_int(cells.get("active"))))
    return buckets


def _build_daily_rows(start_date, end_date, day_counts, spans, buckets):
    """Assemble one row per UTC day in ``[start_date, end_date)``.

    Returns None when every feeding query failed.
    """
    if day_counts is None and spans is None and buckets is None:
        return None
    n_days = (end_date - start_date).days
    days = [start_date + timedelta(days=i) for i in range(n_days)]
    minutes_by_day = _minutes_by_day(spans, days)
    peak_by_day = _peak_by_day(buckets)

    rows = []
    for day in days:
        counts = None if day_counts is None else day_counts.get(day, {})
        rows.append(
            {
                "date": day.isoformat(),
                "users": None if counts is None else counts.get("users", 0),
                "sessions": None if counts is None else counts.get("sessions", 0),
                "member_minutes": (
                    None if minutes_by_day is None else round(minutes_by_day.get(day, 0.0))
                ),
                "peak_concurrent": None if peak_by_day is None else peak_by_day.get(day, 0),
            }
        )
    return rows


def _minutes_by_day(spans, days):
    """Sum per-session minutes (floored at 1) by the UTC day the session began."""
    if spans is None:
        return None
    in_window = set(days)
    minutes = {}
    for _user_id, first_ms, last_ms in spans:
        day = datetime.fromtimestamp(first_ms / 1000.0, tz=timezone.utc).date()  # noqa: UP017
        if day not in in_window:
            logger.debug("Session starting %s falls outside report window; skipped", day)
            continue
        span = max((last_ms - first_ms) / _MS_PER_MINUTE, _MIN_SESSION_MINUTES)
        minutes[day] = minutes.get(day, 0.0) + span
    return minutes


def _peak_by_day(buckets):
    """Max distinct-session count over the 5-minute buckets of each UTC day."""
    if buckets is None:
        return None
    peaks = {}
    for bucket, active in buckets:
        day = bucket.date()
        peaks[day] = max(peaks.get(day, 0), active)
    return peaks


def _distinct_users(spans):
    """Distinct user IDs across all sessions in the window, or None."""
    if spans is None:
        return None
    return len({user_id for user_id, _first, _last in spans if user_id})


def _sum_daily(daily, key):
    """Sum a column of the daily table; None if unavailable."""
    if daily is None or any(row[key] is None for row in daily):
        return None
    return sum(row[key] for row in daily)


def _max_daily(daily, key):
    """Max of a column of the daily table; None if unavailable."""
    if daily is None or any(row[key] is None for row in daily):
        return None
    return max((row[key] for row in daily), default=0)


def _avg_session_minutes(member_minutes, session_count):
    """Mean session length in minutes (1 decimal), or None."""
    if member_minutes is None or not session_count:
        return None
    return round(member_minutes / session_count, 1)


def _row_dicts(rows):
    """Yield each Logs Insights result row as a ``{field: value}`` dict."""
    for row in rows:
        yield {cell["field"]: cell["value"] for cell in row}


def _parse_insights_ts(value):
    """Parse a Logs Insights datefloor/bin cell (UTC) into a datetime, or None."""
    if not value:
        return None
    for fmt in (_INSIGHTS_TS_FORMAT, "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)  # noqa: UP017
        except ValueError:
            continue
    return None


def _query_event_counts(logs_client, log_group, start_ts, end_ts):
    """Return ``{event_name: count}`` for all custom events in the window."""
    query = (
        f"fields event_type as event_name\n"
        f"| filter event_type in [{_TRACKED_EVENTS_QUERY_LIST}]\n"
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
    if event_name_filter is not None:
        parts = [f'filter event_type = "{event_name_filter}"']
    else:
        parts = [f"filter event_type in [{_TRACKED_EVENTS_QUERY_LIST}]"]
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
    _append_daily_usage(lines, this_week)
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
        ("active_users", "Active users"),
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


def _append_daily_usage(lines, metrics):
    """Add the per-day users / sessions / member-minutes / peak table."""
    lines.append("DAILY USAGE (UTC)")
    lines.append("-" * 40)
    lines.append(_daily_row("Day", "Users", "Sessions", "Minutes", "Peak"))
    daily = metrics.get("daily")
    if daily is None:
        lines.append("  n/a (daily usage queries failed)")
        lines.append("")
        return
    for row in daily:
        label = date.fromisoformat(row["date"]).strftime("%a %b %d")
        lines.append(
            _daily_row(
                label,
                _fmt(row["users"]),
                _fmt(row["sessions"]),
                _fmt(row["member_minutes"]),
                _fmt(row["peak_concurrent"]),
            )
        )
    lines.append(
        _daily_row(
            "Week total",
            _fmt(metrics.get("active_users")),
            _fmt(metrics.get("sessions")),
            _fmt(metrics.get("member_minutes")),
            _fmt(metrics.get("peak_concurrent")),
        )
    )
    lines.append(f"  Avg session length: {_fmt_minutes(metrics.get('avg_session_minutes'))}")
    lines.append("")


def _daily_row(day, users, sessions, minutes, peak):
    """Format one line of the daily usage table."""
    return f"  {day:10s}  {users:>5s}  {sessions:>8s}  {minutes:>7s}  {peak:>4s}"


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
        ("active_users", "Active Users"),
        ("member_minutes", "Member Minutes"),
        ("peak_concurrent", "Peak Concurrent"),
    ]
    for key, label in compare_keys:
        current = this_week.get(key)
        previous = prev_week.get(key)
        change = _fmt_pct_change(previous, current)
        lines.append(f"  {label:15s}  {_fmt(previous):>6s} -> {_fmt(current):>6s}  {change}")
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


def _fmt_minutes(value):
    """Format a minutes value with one decimal, handling None."""
    if value is None:
        return "n/a"
    return f"{value:.1f} min"
