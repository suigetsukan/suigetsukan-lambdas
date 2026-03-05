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

# Simulates API Gateway Cognito authorizer context (required for non-OPTIONS requests)
AUTH_CONTEXT = {"requestContext": {"authorizer": {"claims": {"sub": "test-user"}}}}


def _load_cognito_rest_app():
    spec = importlib.util.spec_from_file_location("cognito_rest_app", COGNITO_REST_APP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_handler_options_returns_204():
    with (
        patch.dict(
            "os.environ",
            {
                "AWS_REGION": "us-west-1",
                "AWS_COGNITO_USER_POOL_ID": "us-west-1_abc123",
                "AWS_SES_SOURCE_EMAIL": "test@example.com",
            },
        ),
        patch("boto3.client"),
    ):
        app = _load_cognito_rest_app()
        event = {"httpMethod": "OPTIONS", "path": "/list"}
        result = app.handler(event, MagicMock())
        assert result["statusCode"] == 204
        assert result["body"] == ""
        assert "Access-Control-Allow-Origin" in result["headers"]


def test_handler_missing_authorizer_returns_401():
    """Require API Gateway Cognito authorizer for non-OPTIONS requests."""
    with (
        patch.dict(
            "os.environ",
            {
                "AWS_REGION": "us-west-1",
                "AWS_COGNITO_USER_POOL_ID": "us-west-1_abc123",
                "AWS_SES_SOURCE_EMAIL": "test@example.com",
            },
        ),
        patch("boto3.client"),
    ):
        app = _load_cognito_rest_app()
        event = {"httpMethod": "GET", "path": "/list"}
        result = app.handler(event, MagicMock())
        assert result["statusCode"] == 401
        body = json.loads(result["body"])
        assert "Unauthorized" in body["error"]


def test_handler_missing_httpmethod_returns_400():
    with (
        patch.dict(
            "os.environ",
            {
                "AWS_REGION": "us-west-1",
                "AWS_COGNITO_USER_POOL_ID": "us-west-1_abc123",
                "AWS_SES_SOURCE_EMAIL": "test@example.com",
            },
        ),
        patch("boto3.client"),
    ):
        app = _load_cognito_rest_app()
        event = {"path": "/list"}
        result = app.handler(event, MagicMock())
        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "Missing httpMethod or path" in body["error"]


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
        event = {"httpMethod": "GET", "path": "/list", **AUTH_CONTEXT}
        result = app.handler(event, MagicMock())
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "approved" in body
        assert "unapproved" in body


def test_handler_post_missing_body_returns_400():
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
        cognito_mock.list_users_in_group.return_value = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "Users": [],
        }
        mock_boto.return_value = cognito_mock

        app = _load_cognito_rest_app()
        event = {"httpMethod": "POST", "path": "/approve", "body": None, **AUTH_CONTEXT}
        result = app.handler(event, MagicMock())
        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "error" in body
        assert "Missing body" in body["error"]


def test_handler_post_invalid_json_returns_400():
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
        cognito_mock.list_users_in_group.return_value = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "Users": [],
        }
        mock_boto.return_value = cognito_mock

        app = _load_cognito_rest_app()
        event = {"httpMethod": "POST", "path": "/approve", "body": "{invalid", **AUTH_CONTEXT}
        result = app.handler(event, MagicMock())
        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "error" in body
        assert "Invalid JSON" in body["error"]


def test_handler_post_missing_required_fields_returns_400():
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
        cognito_mock.list_users_in_group.return_value = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "Users": [],
        }
        mock_boto.return_value = cognito_mock

        app = _load_cognito_rest_app()
        event = {
            "httpMethod": "POST",
            "path": "/approve",
            "body": json.dumps({"user": "someuser"}),
            **AUTH_CONTEXT,
        }
        result = app.handler(event, MagicMock())
        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "error" in body
        assert "Missing required fields" in body["error"]


def test_handler_get_list_admin_returns_admin_emails():
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
        cognito_mock.list_users_in_group.return_value = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "Users": [
                {
                    "Username": "admin1",
                    "Attributes": [{"Name": "email", "Value": "admin@example.com"}],
                },
            ],
        }
        mock_boto.return_value = cognito_mock

        app = _load_cognito_rest_app()
        event = {"httpMethod": "GET", "path": "/list/admin", **AUTH_CONTEXT}
        result = app.handler(event, MagicMock())
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body == ["admin@example.com"]


def _cognito_mock_for_post_actions():
    """Cognito mock that supports list_users_in_group by GroupName and admin_* calls."""
    cognito_mock = MagicMock()
    cognito_mock.list_users_in_group.side_effect = lambda **kw: (
        {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "Users": [
                {
                    "Username": "testuser",
                    "Attributes": [{"Name": "email", "Value": "user@example.com"}],
                },
            ],
        }
        if kw.get("GroupName") == "admin"
        else {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "Users": [
                {
                    "Username": "testuser",
                    "Attributes": [{"Name": "email", "Value": "user@example.com"}],
                },
            ],
        }
    )
    cognito_mock.admin_add_user_to_group.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
    cognito_mock.admin_remove_user_from_group.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 200},
    }
    cognito_mock.admin_delete_user.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
    cognito_mock.list_users.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 200},
        "Users": [
            {
                "Username": "testuser",
                "Attributes": [{"Name": "email", "Value": "user@example.com"}],
            },
        ],
    }
    return cognito_mock


def _post_body():
    return json.dumps({
        "user": "testuser",
        "user_email": "user@example.com",
        "admin_email": "admin@example.com",
    })


@pytest.mark.parametrize(
    "path_suffix,expected_substring",
    [
        ("/approve", "approved"),
        ("/promote", "promoted"),
        ("/deny", "denied"),
        ("/close", "closed"),
        ("/delete", "deleted"),
    ],
)
def test_handler_post_actions_return_200(path_suffix, expected_substring):
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
        mock_boto.return_value = _cognito_mock_for_post_actions()
        app = _load_cognito_rest_app()
        with patch.object(app, "send_mail"):
            event = {"httpMethod": "POST", "path": path_suffix, "body": _post_body(), **AUTH_CONTEXT}
            result = app.handler(event, MagicMock())
        assert result["statusCode"] == 200
        body_str = result["body"]
        assert expected_substring in body_str.lower()


def test_handler_get_invalid_path_raises():
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
        cognito_mock.list_users.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}, "Users": []}
        cognito_mock.list_users_in_group.return_value = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "Users": [],
        }
        mock_boto.return_value = cognito_mock

        app = _load_cognito_rest_app()
        event = {"httpMethod": "GET", "path": "/invalid", **AUTH_CONTEXT}
        with pytest.raises(RuntimeError, match="Invalid GET path"):
            app.handler(event, MagicMock())


def test_handler_post_invalid_path_raises():
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
        # get_admin_users is called first; must return at least one admin email
        cognito_mock.list_users_in_group.return_value = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "Users": [
                {
                    "Username": "admin1",
                    "Attributes": [{"Name": "email", "Value": "admin@example.com"}],
                },
            ],
        }
        mock_boto.return_value = cognito_mock

        app = _load_cognito_rest_app()
        event = {"httpMethod": "POST", "path": "/invalid", "body": _post_body(), **AUTH_CONTEXT}
        with pytest.raises(RuntimeError, match="Invalid POST path"):
            app.handler(event, MagicMock())
