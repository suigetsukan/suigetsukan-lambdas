"""
Tests for analytics-report Lambda.
"""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
ANALYTICS_APP = REPO_ROOT / "lambdas" / "analytics-report" / "app.py"


def _load_app():
    spec = importlib.util.spec_from_file_location(
        "analytics_app",
        ANALYTICS_APP,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_kpi_response(value):
    """Build a minimal Pinpoint KPI response with the given value."""
    return {
        "ApplicationDateRangeKpiResponse": {
            "KpiResult": {
                "Rows": [
                    {"Values": [{"Value": str(value)}]},
                ],
            },
        },
    }


def test_lambda_handler_publishes_report():
    """Handler should query Pinpoint and publish to SNS."""
    pinpoint_mock = MagicMock()
    pinpoint_mock.get_application_date_range_kpi.return_value = _make_kpi_response(42)
    sns_mock = MagicMock()

    def _client_factory(service, region_name=None):
        if service == "pinpoint":
            return pinpoint_mock
        return sns_mock

    env = {
        "AWS_PINPOINT_APP_ID": "test-app-id",
        "AWS_PINPOINT_REGION": "us-west-2",
        "AWS_SNS_ANALYTICS_TOPIC_ARN": "arn:aws:sns:us-west-1:123:topic",
        "AWS_REGION": "us-west-1",
    }
    with (
        patch("boto3.client", side_effect=_client_factory),
        patch.dict("os.environ", env, clear=False),
    ):
        app = _load_app()
        result = app.lambda_handler({}, MagicMock())

    assert "report_period" in result
    assert "metrics" in result
    assert result["metrics"]["sessions"] == 42
    sns_mock.publish.assert_called_once()
    call_kwargs = sns_mock.publish.call_args[1]
    assert "TopicArn" in call_kwargs
    assert "Suigetsukan Weekly Analytics" in call_kwargs["Subject"]


def test_lambda_handler_handles_kpi_errors():
    """Handler should return None for failed KPIs, not crash."""
    from botocore.exceptions import ClientError

    pinpoint_mock = MagicMock()
    pinpoint_mock.get_application_date_range_kpi.side_effect = ClientError(
        {"Error": {"Code": "NotFoundException", "Message": "nope"}},
        "GetApplicationDateRangeKpi",
    )
    sns_mock = MagicMock()

    def _client_factory(service, region_name=None):
        if service == "pinpoint":
            return pinpoint_mock
        return sns_mock

    env = {
        "AWS_PINPOINT_APP_ID": "test-app-id",
        "AWS_PINPOINT_REGION": "us-west-2",
        "AWS_SNS_ANALYTICS_TOPIC_ARN": "arn:aws:sns:us-west-1:123:topic",
        "AWS_REGION": "us-west-1",
    }
    with (
        patch("boto3.client", side_effect=_client_factory),
        patch.dict("os.environ", env, clear=False),
    ):
        app = _load_app()
        result = app.lambda_handler({}, MagicMock())

    assert result["metrics"]["sessions"] is None
    assert result["metrics"]["PageView"] is None
    sns_mock.publish.assert_called_once()


def test_sum_kpi_rows_empty():
    """Empty rows should return 0."""
    with (
        patch("boto3.client"),
        patch.dict(
            "os.environ",
            {
                "AWS_PINPOINT_APP_ID": "x",
                "AWS_SNS_ANALYTICS_TOPIC_ARN": "x",
            },
            clear=False,
        ),
    ):
        app = _load_app()
    assert app._sum_kpi_rows([]) == 0


def test_sum_kpi_rows_multiple():
    """Multiple rows should be summed."""
    with (
        patch("boto3.client"),
        patch.dict(
            "os.environ",
            {
                "AWS_PINPOINT_APP_ID": "x",
                "AWS_SNS_ANALYTICS_TOPIC_ARN": "x",
            },
            clear=False,
        ),
    ):
        app = _load_app()
    rows = [
        {"Values": [{"Value": "10"}]},
        {"Values": [{"Value": "20"}, {"Value": "5"}]},
    ]
    assert app._sum_kpi_rows(rows) == 35


def test_completion_rate():
    """Completion rate should be calculated correctly."""
    with (
        patch("boto3.client"),
        patch.dict(
            "os.environ",
            {
                "AWS_PINPOINT_APP_ID": "x",
                "AWS_SNS_ANALYTICS_TOPIC_ARN": "x",
            },
            clear=False,
        ),
    ):
        app = _load_app()
    assert app._completion_rate(100, 75) == "75.0%"
    assert app._completion_rate(0, 0) == "n/a"
    assert app._completion_rate(None, 10) == "n/a"


def test_fmt_pct_change():
    """Percentage change formatting should handle edge cases."""
    with (
        patch("boto3.client"),
        patch.dict(
            "os.environ",
            {
                "AWS_PINPOINT_APP_ID": "x",
                "AWS_SNS_ANALYTICS_TOPIC_ARN": "x",
            },
            clear=False,
        ),
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
    pinpoint_mock = MagicMock()
    pinpoint_mock.get_application_date_range_kpi.return_value = _make_kpi_response(10)
    sns_mock = MagicMock()

    def _client_factory(service, region_name=None):
        if service == "pinpoint":
            return pinpoint_mock
        return sns_mock

    env = {
        "AWS_PINPOINT_APP_ID": "test-app-id",
        "AWS_PINPOINT_REGION": "us-west-2",
        "AWS_SNS_ANALYTICS_TOPIC_ARN": "arn:aws:sns:us-west-1:123:topic",
        "AWS_REGION": "us-west-1",
    }
    with (
        patch("boto3.client", side_effect=_client_factory),
        patch.dict("os.environ", env, clear=False),
    ):
        app = _load_app()
        app.lambda_handler({}, MagicMock())

    report_body = sns_mock.publish.call_args[1]["Message"]
    assert "HIGHLIGHTS" in report_body
    assert "TRAFFIC" in report_body
    assert "VIDEO ENGAGEMENT" in report_body
    assert "SECTION VIEWS" in report_body
    assert "WEEK-OVER-WEEK COMPARISON" in report_body
