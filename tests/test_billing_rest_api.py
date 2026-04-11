"""
Basic tests for billing-rest-api Lambda.
"""

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BILLING_APP = REPO_ROOT / "lambdas" / "billing-rest-api" / "app.py"


def _load_billing_app():
    spec = importlib.util.spec_from_file_location("billing_app", BILLING_APP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lambda_handler_returns_valid_response():
    with patch("boto3.client") as mock_client:
        ce_mock = MagicMock()
        ce_mock.get_cost_and_usage.return_value = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "ResultsByTime": [{"Total": {"BlendedCost": {"Amount": "42.50"}}}],
        }
        ce_mock.get_cost_forecast.return_value = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "Total": {"Amount": "55.00"},
        }
        mock_client.return_value = ce_mock
        with patch.dict("os.environ", {"AWS_REGION": "us-west-1"}, clear=False):
            app = _load_billing_app()
            event: dict = {}
            context = MagicMock()
            response = app.lambda_handler(event, context)
    assert response["statusCode"] == 200
    assert "headers" in response
    assert "Access-Control-Allow-Origin" in response["headers"]
    body = json.loads(response["body"])
    assert "this_month" in body
    assert "last_month" in body
    assert "forecast" in body


def test_lambda_handler_handles_empty_results_by_time():
    """New/unused accounts may return empty ResultsByTime."""
    with patch("boto3.client") as mock_client:
        ce_mock = MagicMock()
        ce_mock.get_cost_and_usage.return_value = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "ResultsByTime": [],
        }
        ce_mock.get_cost_forecast.return_value = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "Total": {"Amount": "55.00"},
        }
        mock_client.return_value = ce_mock
        with patch.dict("os.environ", {"AWS_REGION": "us-west-1"}, clear=False):
            app = _load_billing_app()
            response = app.lambda_handler({}, MagicMock())
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["this_month"] == 0.0
    assert body["last_month"] == 0.0


def test_set_leading_zero():
    """set_leading_zero pads single digit, leaves two digits unchanged."""
    with patch.dict("os.environ", {"AWS_REGION": "us-west-1"}, clear=False):
        app = _load_billing_app()
    assert app.set_leading_zero(5) == "05"
    assert app.set_leading_zero(9) == "09"
    assert app.set_leading_zero(10) == "10"
    assert app.set_leading_zero(0) == "00"


def test_lambda_handler_missing_authorizer_returns_401():
    """When requestContext.authorizer is missing (non-OPTIONS), handler returns 401."""
    with patch.dict("os.environ", {"AWS_REGION": "us-west-1"}, clear=False):
        app = _load_billing_app()
    event = {"httpMethod": "GET"}
    response = app.lambda_handler(event, MagicMock())
    assert response["statusCode"] == 401
    body = json.loads(response["body"])
    assert "Unauthorized" in body["error"]
