"""
Tests for analytics-report Lambda.
"""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ANALYTICS_APP = REPO_ROOT / "lambdas" / "analytics-report" / "app.py"

_TEST_ENV = {
    "RUM_LOG_GROUP_NAME": "/aws/vendedlogs/RUMService_test-monitor",
    "RUM_LOG_REGION": "us-west-1",
    "AWS_SNS_ANALYTICS_TOPIC_ARN": "arn:aws:sns:us-west-1:123:topic",
    "AWS_REGION": "us-west-1",
}


def _load_app():
    spec = importlib.util.spec_from_file_location(
        "analytics_app",
        ANALYTICS_APP,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(**fields):
    """Build a Logs Insights result row from field=value kwargs."""
    return [{"field": k, "value": str(v)} for k, v in fields.items()]


# Number of Logs Insights queries _gather_metrics issues per date range:
# event counts, sessions, unique video viewers, daily counts, session
# spans, concurrency buckets.
QUERIES_PER_WEEK = 6


def _week_start():
    """UTC date the handler uses as this week's start (today - 7 days)."""
    return datetime.now(UTC).date() - timedelta(days=7)


def _ts_cell(day, hour=0, minute=0):
    """Logs Insights datefloor/bin cell text for a UTC day + time."""
    return f"{day.isoformat()} {hour:02d}:{minute:02d}:00.000"


def _epoch_ms(day, hour=0, minute=0, second=0):
    """Epoch-ms for a UTC day + time (matches RUM ``event_timestamp``)."""
    dt = datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _make_logs_mock(
    event_counts=None,
    sessions=0,
    unique_video_viewers=0,
    daily_rows=None,
    span_rows=None,
    bucket_rows=None,
):
    """Build a mock CloudWatch Logs client that returns scripted results.

    ``get_query_results`` is an order-dependent side_effect matching the
    query order in ``_gather_metrics``:

      1. event counts      -> rows built from ``event_counts``
      2. sessions          -> ``sessions``
      3. unique viewers    -> ``unique_video_viewers``
      4. daily counts      -> ``daily_rows`` (day/users/sessions)
      5. session spans     -> ``span_rows`` (session_id/user_id/first_ts/last_ts)
      6. concurrency       -> ``bucket_rows`` (bucket/active)

    The same six responses are replayed for the previous-week range.
    """
    logs = MagicMock()
    logs.start_query.return_value = {"queryId": "q-1"}

    counts_rows = [_row(event_name=k, event_count=v) for k, v in (event_counts or {}).items()]
    responses = [
        {"status": "Complete", "results": counts_rows},
        {"status": "Complete", "results": [_row(n=sessions)]},
        {"status": "Complete", "results": [_row(n=unique_video_viewers)]},
        {"status": "Complete", "results": daily_rows or []},
        {"status": "Complete", "results": span_rows or []},
        {"status": "Complete", "results": bucket_rows or []},
    ]
    assert len(responses) == QUERIES_PER_WEEK
    # Two date ranges (this week + prev week) -> 12 total query results
    logs.get_query_results.side_effect = responses + responses
    return logs


def _client_factory(logs_mock, sns_mock):
    def factory(service, region_name=None):
        if service == "logs":
            return logs_mock
        return sns_mock

    return factory


def test_lambda_handler_publishes_report():
    """Handler should query RUM logs and publish to SNS."""
    counts = {
        "PageView": 42,
        "SectionView": 10,
        "VideoPlay": 8,
        "VideoComplete": 5,
        "VideoPause": 3,
        "UserSignIn": 4,
        "UserSignOut": 4,
    }
    logs_mock = _make_logs_mock(event_counts=counts, sessions=12, unique_video_viewers=6)
    sns_mock = MagicMock()

    with (
        patch("boto3.client", side_effect=_client_factory(logs_mock, sns_mock)),
        patch.dict("os.environ", _TEST_ENV, clear=False),
    ):
        app = _load_app()
        result = app.lambda_handler({}, MagicMock())

    assert "report_period" in result
    assert "metrics" in result
    assert result["metrics"]["PageView"] == 42
    assert result["metrics"]["sessions"] == 12
    assert result["metrics"]["unique_video_viewers"] == 6
    sns_mock.publish.assert_called_once()
    call_kwargs = sns_mock.publish.call_args[1]
    assert "TopicArn" in call_kwargs
    assert "Suigetsukan Weekly Analytics" in call_kwargs["Subject"]

    # Sanity-check that we issued a Logs Insights query against the configured group
    first_call = logs_mock.start_query.call_args_list[0]
    assert first_call.kwargs["logGroupName"] == _TEST_ENV["RUM_LOG_GROUP_NAME"]

    # Custom events are stored with the event name as the TOP-LEVEL
    # event_type in the RUM vended log group -- the query must filter on
    # that, not on a com.amazon.rum.custom_event wrapper.
    query = first_call.kwargs["queryString"]
    assert '"PageView"' in query
    assert "com.amazon.rum.custom_event" not in query


def test_lambda_handler_raises_when_all_queries_denied():
    """If every Logs Insights query fails, the handler must raise instead of
    publishing an empty report (so the Lambda Errors metric fires)."""
    from botocore.exceptions import ClientError

    logs_mock = MagicMock()
    logs_mock.start_query.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "nope"}},
        "StartQuery",
    )
    sns_mock = MagicMock()

    with (
        patch("boto3.client", side_effect=_client_factory(logs_mock, sns_mock)),
        patch.dict("os.environ", _TEST_ENV, clear=False),
    ):
        app = _load_app()
        with pytest.raises(RuntimeError, match="All Logs Insights queries failed"):
            app.lambda_handler({}, MagicMock())

    sns_mock.publish.assert_not_called()


def test_lambda_handler_raises_when_all_queries_fail_status():
    """All queries ending in 'Failed' status must also abort the publish."""
    logs_mock = MagicMock()
    logs_mock.start_query.return_value = {"queryId": "q-1"}
    logs_mock.get_query_results.return_value = {"status": "Failed", "results": []}
    sns_mock = MagicMock()

    with (
        patch("boto3.client", side_effect=_client_factory(logs_mock, sns_mock)),
        patch.dict("os.environ", _TEST_ENV, clear=False),
    ):
        app = _load_app()
        with pytest.raises(RuntimeError, match="All Logs Insights queries failed"):
            app.lambda_handler({}, MagicMock())

    sns_mock.publish.assert_not_called()


def test_lambda_handler_publishes_on_partial_failure():
    """A single failed query should NOT block the report; the affected
    metric is n/a while the rest publish normally."""
    from botocore.exceptions import ClientError

    logs_mock = MagicMock()
    denied = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "nope"}},
        "StartQuery",
    )
    # First query (event counts, this week) fails; the rest succeed.
    logs_mock.start_query.side_effect = [denied] + [{"queryId": "q-1"}] * (2 * QUERIES_PER_WEEK - 1)
    logs_mock.get_query_results.return_value = {
        "status": "Complete",
        "results": [_row(n=3)],
    }
    sns_mock = MagicMock()

    with (
        patch("boto3.client", side_effect=_client_factory(logs_mock, sns_mock)),
        patch.dict("os.environ", _TEST_ENV, clear=False),
    ):
        app = _load_app()
        result = app.lambda_handler({}, MagicMock())

    assert result["metrics"]["PageView"] is None
    assert result["metrics"]["sessions"] == 3
    sns_mock.publish.assert_called_once()


def test_missing_events_default_to_zero_not_none():
    """Events absent from query results should count as 0 (query ran fine)."""
    logs_mock = _make_logs_mock(
        event_counts={"PageView": 5},  # only PageView present
        sessions=1,
        unique_video_viewers=0,
    )
    sns_mock = MagicMock()

    with (
        patch("boto3.client", side_effect=_client_factory(logs_mock, sns_mock)),
        patch.dict("os.environ", _TEST_ENV, clear=False),
    ):
        app = _load_app()
        result = app.lambda_handler({}, MagicMock())

    assert result["metrics"]["PageView"] == 5
    assert result["metrics"]["VideoPlay"] == 0
    assert result["metrics"]["SectionView"] == 0


def test_completion_rate():
    """Completion rate should be calculated correctly."""
    with (
        patch("boto3.client"),
        patch.dict("os.environ", _TEST_ENV, clear=False),
    ):
        app = _load_app()
    assert app._completion_rate(100, 75) == "75.0%"
    assert app._completion_rate(0, 0) == "n/a"
    assert app._completion_rate(None, 10) == "n/a"


def test_fmt_pct_change():
    """Percentage change formatting should handle edge cases."""
    with (
        patch("boto3.client"),
        patch.dict("os.environ", _TEST_ENV, clear=False),
    ):
        app = _load_app()
    assert app._fmt_pct_change(100, 120) == "(+20% **)"
    assert app._fmt_pct_change(100, 110) == "(+10%)"
    assert app._fmt_pct_change(100, 50) == "(-50% **)"
    assert app._fmt_pct_change(None, 10) == "(n/a)"
    assert app._fmt_pct_change(0, 5) == "(new)"
    assert app._fmt_pct_change(0, 0) == "(n/a)"


def test_report_contains_all_sections():
    """Report should include all expected section headers."""
    logs_mock = _make_logs_mock(event_counts={"PageView": 10}, sessions=1, unique_video_viewers=1)
    sns_mock = MagicMock()

    with (
        patch("boto3.client", side_effect=_client_factory(logs_mock, sns_mock)),
        patch.dict("os.environ", _TEST_ENV, clear=False),
    ):
        app = _load_app()
        app.lambda_handler({}, MagicMock())

    report_body = sns_mock.publish.call_args[1]["Message"]
    assert "HIGHLIGHTS" in report_body
    assert "TRAFFIC" in report_body
    assert "VIDEO ENGAGEMENT" in report_body
    assert "SECTION VIEWS" in report_body
    assert "DAILY USAGE (UTC)" in report_body
    assert "WEEK-OVER-WEEK COMPARISON" in report_body


# -------------------------------------------------------------------
#  Daily usage
# -------------------------------------------------------------------


def _run_handler(logs_mock):
    """Run the handler against a scripted logs mock; return (result, body)."""
    sns_mock = MagicMock()
    with (
        patch("boto3.client", side_effect=_client_factory(logs_mock, sns_mock)),
        patch.dict("os.environ", _TEST_ENV, clear=False),
    ):
        app = _load_app()
        result = app.lambda_handler({}, MagicMock())
    sns_mock.publish.assert_called_once()
    return result, sns_mock.publish.call_args[1]["Message"]


def test_daily_usage_table_seven_days():
    """A full week of data renders one row per UTC day plus rollups."""
    start = _week_start()
    days = [start + timedelta(days=i) for i in range(7)]
    daily_rows = [_row(day=_ts_cell(d), users=1, sessions=2) for d in days]
    span_rows = []
    bucket_rows = []
    for i, d in enumerate(days):
        # Two sessions/day: 4 min and 10 min, from two users.
        span_rows.append(
            _row(
                session_id=f"s{i}a",
                user_id="u1",
                first_ts=_epoch_ms(d, 10, 0),
                last_ts=_epoch_ms(d, 10, 4),
            )
        )
        span_rows.append(
            _row(
                session_id=f"s{i}b",
                user_id="u2",
                first_ts=_epoch_ms(d, 11, 0),
                last_ts=_epoch_ms(d, 11, 10),
            )
        )
        bucket_rows.append(_row(bucket=_ts_cell(d, 10, 0), active=1))
        bucket_rows.append(_row(bucket=_ts_cell(d, 11, 0), active=2 if i == 3 else 1))

    logs_mock = _make_logs_mock(
        event_counts={"PageView": 10},
        sessions=14,
        unique_video_viewers=1,
        daily_rows=daily_rows,
        span_rows=span_rows,
        bucket_rows=bucket_rows,
    )
    result, body = _run_handler(logs_mock)

    daily = result["metrics"]["daily"]
    assert len(daily) == 7
    assert [row["date"] for row in daily] == [d.isoformat() for d in days]
    for row in daily:
        assert row["users"] == 1
        assert row["sessions"] == 2
        assert row["member_minutes"] == 14
    assert [row["peak_concurrent"] for row in daily] == [1, 1, 1, 2, 1, 1, 1]

    assert result["metrics"]["active_users"] == 2
    assert result["metrics"]["member_minutes"] == 98
    assert result["metrics"]["peak_concurrent"] == 2
    assert result["metrics"]["avg_session_minutes"] == 7.0

    assert "DAILY USAGE (UTC)" in body
    assert "  Day         Users  Sessions  Minutes  Peak" in body
    assert f"  {days[3].strftime('%a %b %d')}      1         2       14     2" in body
    assert "  Week total      2        14       98     2" in body
    assert "  Avg session length: 7.0 min" in body
    # The mock replays this week's rows for the previous week, so the
    # span/bucket rows fall outside the prev-week window and roll up to 0.
    assert "  Active Users          2 ->      2  (+0%)" in body
    assert "  Member Minutes        0 ->     98  (new)" in body
    assert "  Peak Concurrent       0 ->      2  (new)" in body


def test_daily_usage_day_with_zero_events():
    """A day absent from every query result renders as zeros, not n/a."""
    start = _week_start()
    busy = start + timedelta(days=1)
    quiet = start + timedelta(days=2)
    logs_mock = _make_logs_mock(
        sessions=1,
        daily_rows=[_row(day=_ts_cell(busy), users=1, sessions=1)],
        span_rows=[
            _row(
                session_id="s1",
                user_id="u1",
                first_ts=_epoch_ms(busy, 9, 0),
                last_ts=_epoch_ms(busy, 9, 3),
            )
        ],
        bucket_rows=[_row(bucket=_ts_cell(busy, 9, 0), active=1)],
    )
    result, body = _run_handler(logs_mock)

    by_date = {row["date"]: row for row in result["metrics"]["daily"]}
    assert by_date[busy.isoformat()] == {
        "date": busy.isoformat(),
        "users": 1,
        "sessions": 1,
        "member_minutes": 3,
        "peak_concurrent": 1,
    }
    assert by_date[quiet.isoformat()] == {
        "date": quiet.isoformat(),
        "users": 0,
        "sessions": 0,
        "member_minutes": 0,
        "peak_concurrent": 0,
    }
    assert f"  {quiet.strftime('%a %b %d')}      0         0        0     0" in body


def test_daily_usage_single_event_session_floors_to_one_minute():
    """A session with one event (first_ts == last_ts) counts as 1 minute."""
    start = _week_start()
    day = start + timedelta(days=4)
    ts = _epoch_ms(day, 15, 30)
    logs_mock = _make_logs_mock(
        sessions=1,
        daily_rows=[_row(day=_ts_cell(day), users=1, sessions=1)],
        span_rows=[_row(session_id="s1", user_id="u1", first_ts=ts, last_ts=ts)],
        bucket_rows=[_row(bucket=_ts_cell(day, 15, 30), active=1)],
    )
    result, _body = _run_handler(logs_mock)

    by_date = {row["date"]: row for row in result["metrics"]["daily"]}
    assert by_date[day.isoformat()]["member_minutes"] == 1
    assert result["metrics"]["member_minutes"] == 1
    assert result["metrics"]["avg_session_minutes"] == 1.0


def test_daily_usage_failed_daily_query_renders_na():
    """If the per-day counts query fails, users/sessions show n/a while the
    span-derived columns still populate and the report still publishes."""
    from botocore.exceptions import ClientError

    start = _week_start()
    day = start + timedelta(days=2)
    logs_mock = _make_logs_mock(
        sessions=1,
        span_rows=[
            _row(
                session_id="s1",
                user_id="u1",
                first_ts=_epoch_ms(day, 8, 0),
                last_ts=_epoch_ms(day, 8, 5),
            )
        ],
        bucket_rows=[_row(bucket=_ts_cell(day, 8, 0), active=1)],
    )
    denied = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "nope"}},
        "StartQuery",
    )
    ok = {"queryId": "q-1"}
    # Query 4 (daily counts) fails in both weeks; every other query succeeds.
    logs_mock.start_query.side_effect = [ok, ok, ok, denied, ok, ok] * 2
    # A denied start_query consumes no get_query_results call, so drop the
    # daily-counts responses from the scripted sequence.
    responses = list(logs_mock.get_query_results.side_effect)
    logs_mock.get_query_results.side_effect = [
        r for i, r in enumerate(responses) if i % QUERIES_PER_WEEK != 3
    ]

    result, body = _run_handler(logs_mock)

    by_date = {row["date"]: row for row in result["metrics"]["daily"]}
    row = by_date[day.isoformat()]
    assert row["users"] is None
    assert row["sessions"] is None
    assert row["member_minutes"] == 5
    assert row["peak_concurrent"] == 1
    assert result["metrics"]["active_users"] == 1
    assert result["metrics"]["member_minutes"] == 5
    assert f"  {day.strftime('%a %b %d')}    n/a       n/a        5     1" in body


def test_daily_usage_all_daily_queries_failed_renders_na():
    """If all three daily queries fail, ``daily`` is None, the section shows
    n/a, and the rest of the report still publishes."""
    logs_mock = _make_logs_mock(event_counts={"PageView": 3}, sessions=1)
    responses = list(logs_mock.get_query_results.side_effect)
    failed = {"status": "Failed", "results": []}
    logs_mock.get_query_results.side_effect = [
        failed if i % QUERIES_PER_WEEK >= 3 else r for i, r in enumerate(responses)
    ]

    result, body = _run_handler(logs_mock)

    assert result["metrics"]["daily"] is None
    assert result["metrics"]["active_users"] is None
    assert result["metrics"]["member_minutes"] is None
    assert result["metrics"]["peak_concurrent"] is None
    assert result["metrics"]["avg_session_minutes"] is None
    assert result["metrics"]["PageView"] == 3
    assert "  n/a (daily usage queries failed)" in body
    assert "  Avg session length" not in body
    assert "  Member Minutes      n/a ->    n/a  (n/a)" in body
