"""
Basic tests for cognito-rest-api Lambda.
"""

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
COGNITO_REST_APP = REPO_ROOT / "lambdas" / "cognito-rest-api" / "app.py"


def _load_cognito_rest_app():
    spec = importlib.util.spec_from_file_location("cognito_rest_app", COGNITO_REST_APP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_handler_get_list_returns_structure():
    with (
        patch.dict(
            "os.environ",
            {
                "AWS_REGION": "us-west-1",
                "AWS_COGNITO_USER_POOL_ID": "us-west-1_abc123",
                "AWS_SES_SOURCE_EMAIL": "test@example.com",
            },
        ),
        patch("boto3.client") as mock_boto,
    ):
        cognito_mock = MagicMock()
        cognito_mock.list_users.return_value = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "Users": [],
        }
        cognito_mock.list_users_in_group.return_value = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "Users": [],
        }
        mock_boto.return_value = cognito_mock

        app = _load_cognito_rest_app()
        event = {"httpMethod": "GET", "path": "/list"}
        result = app.handler(event, MagicMock())
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "approved" in body
        assert "unapproved" in body
