"""
Tests for usage-rest-api Lambda.

Module imports must not touch the network. AWS clients are stubbed via
boto3.client patching; results are scripted per call.
"""

import importlib.util
import json
from datetime import datetime, timezone, UTC
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
USAGE_APP = REPO_ROOT / "lambdas" / "usage-rest-api" / "app.py"

AUTH_CONTEXT = {"requestContext": {"authorizer": {"claims": {"sub": "test"}}}}


def _load_app():
    """Reload the module fresh each test so module-level caches reset."""
    spec = importlib.util.spec_from_file_location("usage_app", USAGE_APP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(**fields):
    return [{"field": k, "value": str(v)} for k, v in fields.items()]


def _make_logs_mock(rows_per_call=None):
    rows_per_call = rows_per_call or [[]]
    logs = MagicMock()
    logs.start_query.return_value = {"queryId": "q-1"}

    def results_factory():
        for rows in rows_per_call:
            yield {"status": "Complete", "results": rows}
        while True:
            yield {"status": "Complete", "results": []}

    gen = results_factory()
    logs.get_query_results.side_effect = lambda **_: next(gen)
    return logs


def _make_cw_mock(per_metric=None):
    """per_metric is {MetricName: [(timestamp, value)]}"""
    per_metric = per_metric or {}
    cw = MagicMock()

    def get_metric_statistics(**kw):
        metric = kw["MetricName"]
        statistic = kw["Statistics"][0]
        points = per_metric.get(metric, [])
        datapoints = [{"Timestamp": ts, statistic: float(value)} for ts, value in points]
        return {"Datapoints": datapoints}

    cw.get_metric_statistics.side_effect = get_metric_statistics
    return cw


def _client_factory(cw, logs):
    def factory(service, region_name=None):
        if service == "cloudwatch":
            return cw
        if service == "logs":
            return logs
        raise AssertionError(f"unexpected client: {service}")

    return factory


@pytest.fixture
def env():
    with patch.dict(
        "os.environ",
        {"AWS_REGION": "us-west-1", "RUM_REGION": "us-west-1"},
        clear=False,
    ):
        import os as _os

        _os.environ.pop("REQUIRE_AUTHORIZER", None)
        _os.environ.pop("CORS_ALLOWED_ORIGIN", None)
        yield


def test_module_imports_without_network(env):
    """Sanity: importing the module does not invoke AWS."""
    with patch("boto3.client") as mock_boto:
        _load_app()
        mock_boto.assert_not_called()


def test_options_returns_204(env):
    with patch("boto3.client"):
        app = _load_app()
        result = app.lambda_handler({"httpMethod": "OPTIONS", "path": "/usage/summary"}, None)
        assert result["statusCode"] == 204
        assert "Access-Control-Allow-Origin" in result["headers"]


def test_unsupported_method_returns_400(env):
    with patch("boto3.client"):
        app = _load_app()
        result = app.lambda_handler({"httpMethod": "POST", "path": "/usage/summary"}, None)
        assert result["statusCode"] == 400


def test_authorizer_required_rejects_when_missing(env):
    with (
        patch.dict("os.environ", {"REQUIRE_AUTHORIZER": "true"}, clear=False),
        patch("boto3.client"),
    ):
        app = _load_app()
        result = app.lambda_handler({"httpMethod": "GET", "path": "/usage/summary"}, None)
        assert result["statusCode"] == 401
        body = json.loads(result["body"])
        assert "Unauthorized" in body["error"]


def test_parse_days_default_and_clamp(env):
    with patch("boto3.client"):
        app = _load_app()
    assert app._parse_days(None) == 7
    assert app._parse_days({}) == 7
    assert app._parse_days({"days": "14"}) == 14
    assert app._parse_days({"days": "0"}) == 1
    assert app._parse_days({"days": "9999"}) == 90
    assert app._parse_days({"days": "junk"}) == 7


def test_summary_returns_metrics(env):
    ts = datetime(2026, 5, 14, tzinfo=UTC)
    cw = _make_cw_mock(
        {
            "SessionCount": [(ts, 5)],
            "PageViewCount": [(ts, 50)],
            "SessionDuration": [(ts, 120.0)],
            "PageViewCountPerSession": [(ts, 10.0)],
            "JsErrorCount": [(ts, 2)],
            "Http4xxCountPerPageView": [(ts, 0.01)],
            "Http5xxCountPerPageView": [(ts, 0.0)],
        }
    )
    logs = _make_logs_mock()
    with patch("boto3.client", side_effect=_client_factory(cw, logs)):
        app = _load_app()
        result = app.lambda_handler(
            {"httpMethod": "GET", "path": "/usage/summary", **AUTH_CONTEXT}, None
        )
    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["data"]["sessions"] == 5
    assert body["data"]["page_views"] == 50
    assert body["data"]["js_error_count"] == 2
    assert body["data"]["daily"]["sessions"][0]["value"] == 5
    assert body["days"] == 7


def test_summary_empty_metrics(env):
    cw = _make_cw_mock({})
    logs = _make_logs_mock()
    with patch("boto3.client", side_effect=_client_factory(cw, logs)):
        app = _load_app()
        result = app.lambda_handler(
            {"httpMethod": "GET", "path": "/usage/summary", **AUTH_CONTEXT}, None
        )
    body = json.loads(result["body"])
    assert body["data"]["sessions"] == 0
    assert body["data"]["avg_session_duration_seconds"] is None


def test_webvitals_uses_inp_when_present(env):
    ts = datetime(2026, 5, 14, tzinfo=UTC)
    cw = _make_cw_mock(
        {
            "WebVitalsLargestContentfulPaint": [(ts, 1.2)],
            "WebVitalsCumulativeLayoutShift": [(ts, 0.05)],
            "WebVitalsInteractionToNextPaint": [(ts, 80.0)],
            "WebVitalsFirstInputDelay": [(ts, 999.0)],
        }
    )
    logs = _make_logs_mock()
    with patch("boto3.client", side_effect=_client_factory(cw, logs)):
        app = _load_app()
        result = app.lambda_handler(
            {"httpMethod": "GET", "path": "/usage/webvitals", **AUTH_CONTEXT}, None
        )
    body = json.loads(result["body"])
    assert body["data"]["lcp_seconds"] == pytest.approx(1.2)
    assert body["data"]["interaction_ms"] == pytest.approx(80.0)


def test_top_pages(env):
    cw = _make_cw_mock({})
    logs = _make_logs_mock(
        [
            [
                _row(**{"event_details.page": "/Aikido/Techniques", "views": 30}),
                _row(**{"event_details.page": "/Home", "views": 12}),
            ]
        ]
    )
    with patch("boto3.client", side_effect=_client_factory(cw, logs)):
        app = _load_app()
        result = app.lambda_handler(
            {"httpMethod": "GET", "path": "/usage/topPages", **AUTH_CONTEXT}, None
        )
    body = json.loads(result["body"])
    assert body["data"][0] == {"page": "/Aikido/Techniques", "views": 30}


def test_top_pages_empty_returns_note(env):
    cw = _make_cw_mock({})
    logs = _make_logs_mock([[]])
    with patch("boto3.client", side_effect=_client_factory(cw, logs)):
        app = _load_app()
        result = app.lambda_handler(
            {"httpMethod": "GET", "path": "/usage/topPages", **AUTH_CONTEXT}, None
        )
    body = json.loads(result["body"])
    assert body["data"] == []
    assert body["note"] == "no events in window"


def test_top_videos_aggregates_per_technique(env):
    cw = _make_cw_mock({})
    logs = _make_logs_mock(
        [
            [
                _row(
                    **{
                        "event_details.event_type": "VideoPlay",
                        "event_details.technique": "Tenchi Nage",
                        "cnt": 4,
                        "avg_watched": 30,
                    }
                ),
                _row(
                    **{
                        "event_details.event_type": "VideoComplete",
                        "event_details.technique": "Tenchi Nage",
                        "cnt": 2,
                        "avg_watched": 90,
                    }
                ),
                _row(
                    **{
                        "event_details.event_type": "VideoPause",
                        "event_details.technique": "Shihonage",
                        "cnt": 1,
                        "avg_watched": 12,
                    }
                ),
            ]
        ]
    )
    with patch("boto3.client", side_effect=_client_factory(cw, logs)):
        app = _load_app()
        result = app.lambda_handler(
            {"httpMethod": "GET", "path": "/usage/topVideos", **AUTH_CONTEXT}, None
        )
    body = json.loads(result["body"])
    by_tech = {row["technique"]: row for row in body["data"]}
    assert by_tech["Tenchi Nage"]["plays"] == 4
    assert by_tech["Tenchi Nage"]["completes"] == 2
    # 4 events at avg 30 + 2 events at avg 90 => weighted avg 50
    assert by_tech["Tenchi Nage"]["avg_watched_seconds"] == 50.0


def test_top_errors(env):
    cw = _make_cw_mock({})
    logs = _make_logs_mock(
        [
            [
                _row(
                    **{
                        "event_details.message": "TypeError: undefined is not a function",
                        "metadata.pageId": "/Aikido",
                        "count": 3,
                    }
                ),
            ]
        ]
    )
    with patch("boto3.client", side_effect=_client_factory(cw, logs)):
        app = _load_app()
        result = app.lambda_handler(
            {"httpMethod": "GET", "path": "/usage/topErrors", **AUTH_CONTEXT}, None
        )
    body = json.loads(result["body"])
    assert body["data"][0]["count"] == 3
    assert body["data"][0]["page"] == "/Aikido"


def test_devices_combines_two_queries(env):
    cw = _make_cw_mock({})
    logs = _make_logs_mock(
        [
            [
                _row(**{"metadata.deviceType": "desktop", "views": 40}),
                _row(**{"metadata.deviceType": "mobile", "views": 15}),
            ],
            [
                _row(**{"metadata.browserName": "Chrome", "views": 30}),
            ],
        ]
    )
    with patch("boto3.client", side_effect=_client_factory(cw, logs)):
        app = _load_app()
        result = app.lambda_handler(
            {"httpMethod": "GET", "path": "/usage/devices", **AUTH_CONTEXT}, None
        )
    body = json.loads(result["body"])
    assert body["data"]["devices"][0] == {"device": "desktop", "views": 40}
    assert body["data"]["browsers"][0] == {"browser": "Chrome", "views": 30}


def test_geography(env):
    cw = _make_cw_mock({})
    logs = _make_logs_mock(
        [
            [_row(**{"metadata.countryCode": "US", "views": 20})],
            [
                _row(
                    **{
                        "metadata.countryCode": "US",
                        "metadata.subdivisionCode": "CA",
                        "views": 12,
                    }
                ),
            ],
        ]
    )
    with patch("boto3.client", side_effect=_client_factory(cw, logs)):
        app = _load_app()
        result = app.lambda_handler(
            {"httpMethod": "GET", "path": "/usage/geography", **AUTH_CONTEXT}, None
        )
    body = json.loads(result["body"])
    assert body["data"]["countries"][0]["country"] == "US"
    assert body["data"]["regions"][0]["region"] == "CA"


def test_unknown_endpoint_returns_error(env):
    cw = _make_cw_mock({})
    logs = _make_logs_mock()
    with patch("boto3.client", side_effect=_client_factory(cw, logs)):
        app = _load_app()
        result = app.lambda_handler(
            {"httpMethod": "GET", "path": "/usage/bogus", **AUTH_CONTEXT}, None
        )
    body = json.loads(result["body"])
    assert body["data"] is None
    assert "Unknown endpoint" in body["error"]


def test_cache_hit_skips_second_aws_call(env):
    ts = datetime(2026, 5, 14, tzinfo=UTC)
    cw = _make_cw_mock({"SessionCount": [(ts, 5)]})
    logs = _make_logs_mock()
    with patch("boto3.client", side_effect=_client_factory(cw, logs)):
        app = _load_app()
        event = {"httpMethod": "GET", "path": "/usage/summary", **AUTH_CONTEXT}
        app.lambda_handler(event, None)
        calls_after_first = cw.get_metric_statistics.call_count
        assert calls_after_first > 0
        app.lambda_handler(event, None)
        assert cw.get_metric_statistics.call_count == calls_after_first


def test_aws_failure_returns_data_null_error(env):
    from botocore.exceptions import ClientError

    cw = MagicMock()
    cw.get_metric_statistics.side_effect = ClientError(
        {"Error": {"Code": "Throttling", "Message": "rate limit"}},
        "GetMetricStatistics",
    )
    logs = _make_logs_mock()
    with patch("boto3.client", side_effect=_client_factory(cw, logs)):
        app = _load_app()
        result = app.lambda_handler(
            {"httpMethod": "GET", "path": "/usage/summary", **AUTH_CONTEXT}, None
        )
    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["data"] is None
    assert "error" in body
