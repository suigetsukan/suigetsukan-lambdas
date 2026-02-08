"""
Basic tests for cognito-post-confirmation Lambda.
"""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
COGNITO_POST_APP = REPO_ROOT / "lambdas" / "cognito-post-confirmation" / "app.py"


def _load_cognito_post_app():
    spec = importlib.util.spec_from_file_location("cognito_post_app", COGNITO_POST_APP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_handler_post_confirmation_returns_event():
    with (
        patch.dict(
            "os.environ", {"AWS_REGION": "us-west-1", "AWS_SES_SOURCE_EMAIL": "test@example.com"}
        ),
        patch("boto3.client") as mock_boto,
    ):
        cognito_mock = MagicMock()
        cognito_mock.admin_add_user_to_group.return_value = {
            "ResponseMetadata": {"HTTPStatusCode": 200}
        }
        cognito_mock.list_users_in_group.return_value = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "Users": [{"Attributes": [{"Name": "email", "Value": "admin@example.com"}]}],
        }
        ses_mock = MagicMock()
        ses_mock.send_email.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
        mock_boto.side_effect = lambda svc, **kw: cognito_mock if "cognito" in svc else ses_mock

        app = _load_cognito_post_app()
        event = {
            "triggerSource": "PostConfirmation_ConfirmSignUp",
            "userName": "testuser",
            "userPoolId": "us-west-1_abc123",
            "request": {"userAttributes": {"email": "newuser@example.com"}},
        }
        result = app.handler(event, MagicMock())
        assert result == event


def test_handler_missing_trigger_source_raises():
    with patch.dict("os.environ", {"AWS_REGION": "us-west-1"}):
        app = _load_cognito_post_app()
        event: dict = {}
        with pytest.raises(ValueError, match="missing triggerSource"):
            app.handler(event, MagicMock())


def test_handler_other_trigger_passes_through():
    with patch.dict("os.environ", {"AWS_REGION": "us-west-1"}):
        app = _load_cognito_post_app()
        event = {
            "triggerSource": "PreSignUp_SignUp",
            "userName": "testuser",
            "userPoolId": "us-west-1_abc123",
        }
        result = app.handler(event, MagicMock())
        assert result == event
