"""
Tests for analytics-report Lambda.
"""

import importlib.util
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


def _make_logs_mock(event_counts=None, sessions=0, unique_video_viewers=0):
    """Build a mock CloudWatch Logs client that returns scripted results.

    ``event_counts`` is a {event_name: count} dict for the first query.
    Subsequent queries return ``sessions`` then ``unique_video_viewers``.
    """
    logs = MagicMock()
    logs.start_query.return_value = {"queryId": "q-1"}

    counts_rows = [_row(event_name=k, event_count=v) for k, v in (event_counts or {}).items()]
    responses = [
        {"status": "Complete", "results": counts_rows},
        {"status": "Complete", "results": [_row(n=sessions)]},
        {"status": "Complete", "results": [_row(n=unique_video_viewers)]},
    ]
    # Two date ranges (this week + prev week) -> 6 total query results
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
    logs_mock.start_query.side_effect = [denied] + [{"queryId": "q-1"}] * 5
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
    assert "WEEK-OVER-WEEK COMPARISON" in report_body
