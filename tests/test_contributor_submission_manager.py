"""
Tests for the contributor-submission-manager Lambda.

The Lambda is dispatched via API Gateway (Lambda proxy integration) for the
six REST endpoints and via S3 ObjectCreated for the seeding side-channel.
We exercise both shapes here.

Style follows ``test_cognito_rest_api.py``: load the module via importlib so
no fragile sys.path tricks leak across tests, use ``unittest.mock`` for
boto3 stubs (no moto in this repo). Each test stands up the env vars the
module reads at import / runtime.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAMBDA_DIR = REPO_ROOT / "lambdas" / "contributor-submission-manager"


CONTRIBUTOR_CLAIMS = {
    "sub": "user-abc",
    "email": "yamamoto@example.com",
    "cognito:groups": ["contributor"],
}

APPROVER_CLAIMS = {
    "sub": "approver-def",
    "email": "sensei@example.com",
    "cognito:groups": ["approver", "admin"],
}


def _api_event(method: str, path: str, *, claims: dict | None, **kwargs) -> dict:
    """Build a minimal API Gateway Lambda-proxy event."""
    event: dict = {
        "httpMethod": method,
        "path": path,
        "resource": kwargs.pop("resource", path),
        "headers": {},
        "queryStringParameters": kwargs.pop("queryStringParameters", None),
        "pathParameters": kwargs.pop("pathParameters", None),
        "body": kwargs.pop("body", None),
        "isBase64Encoded": False,
    }
    if claims is not None:
        event["requestContext"] = {"authorizer": {"claims": claims}}
    return event


def _s3_event(bucket: str, key: str) -> dict:
    return {
        "Records": [
            {
                "eventSource": "aws:s3",
                "s3": {
                    "bucket": {"name": bucket},
                    "object": {"key": key},
                },
            }
        ]
    }


@pytest.fixture
def env_vars():
    """Standard env vars required by the Lambda at runtime."""
    return {
        "AWS_REGION": "us-west-1",
        "AWS_COGNITO_USER_POOL_ID": "us-west-1_pool",
        "AWS_SES_SOURCE_EMAIL": "noreply@example.com",
        "CONTRIBUTOR_SUBMISSIONS_TABLE": "contributor_submissions_test",
        "CONTRIBUTOR_SUBMISSIONS_BUCKET": "test-submissions-bucket",
        "APPROVED_FOR_TRANSCODING_BUCKET": "test-approved-bucket",
    }


_OWNED_MODULE_NAMES = (
    "app_contributor_submission_manager",
    "app",
    "auth",
    "presign",
    "responses",
    "submissions",
)


@pytest.fixture
def app_module(env_vars):
    """Load the Lambda's app module fresh for each test, then clean up.

    The Lambda's siblings (``auth``, ``presign`` etc.) are unqualified
    imports — same as how the Lambda runtime resolves them when the zip is
    expanded into ``$LAMBDA_TASK_ROOT``. To mirror that, we temporarily put
    the Lambda directory on ``sys.path`` and drop cached module entries
    before importing. Cleanup is essential: ``test_file_name_decipher.py``
    also does ``import app`` (resolved against ``lambdas/file-name-decipher``
    via conftest's path setup), so leaving our directory on ``sys.path``
    would silently shadow its ``app`` module on the next test.
    """
    lambda_dir_str = str(LAMBDA_DIR)
    added_path = lambda_dir_str not in sys.path
    if added_path:
        sys.path.insert(0, lambda_dir_str)
    previous_modules = {name: sys.modules.pop(name, None) for name in _OWNED_MODULE_NAMES}
    try:
        with patch.dict("os.environ", env_vars, clear=False):
            spec = importlib.util.spec_from_file_location(
                "app_contributor_submission_manager", LAMBDA_DIR / "app.py"
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules["app_contributor_submission_manager"] = module
            spec.loader.exec_module(module)
            yield module
    finally:
        if added_path:
            with contextlib.suppress(ValueError):
                sys.path.remove(lambda_dir_str)
        for name in _OWNED_MODULE_NAMES:
            sys.modules.pop(name, None)
        for name, previous in previous_modules.items():
            if previous is not None:
                sys.modules[name] = previous


# ---------------------------------------------------------------------------
# Dispatcher / auth gate
# ---------------------------------------------------------------------------


def test_options_request_returns_204(app_module):
    result = app_module.handler(
        _api_event("OPTIONS", "/contributor/submissions", claims=None), None
    )
    assert result["statusCode"] == 204
    assert "Access-Control-Allow-Origin" in result["headers"]


def test_unauthenticated_request_rejected_when_authorizer_required(app_module):
    result = app_module.handler(_api_event("GET", "/contributor/submissions", claims=None), None)
    assert result["statusCode"] == 401
    assert "Unauthorized" in json.loads(result["body"])["error"]


def test_unknown_route_returns_404(app_module):
    result = app_module.handler(_api_event("GET", "/nope/nada", claims=CONTRIBUTOR_CLAIMS), None)
    assert result["statusCode"] == 404


def test_contributor_group_required_on_contributor_routes(app_module):
    # Caller is authenticated but not in the contributor group.
    claims = {"sub": "user-xyz", "cognito:groups": ["approved"]}
    result = app_module.handler(_api_event("GET", "/contributor/submissions", claims=claims), None)
    assert result["statusCode"] == 403
    body = json.loads(result["body"])
    assert "contributor" in body["error"]


def test_approver_group_required_on_admin_routes(app_module):
    # Authenticated contributor cannot hit /administration/*
    result = app_module.handler(
        _api_event("GET", "/administration/submissions", claims=CONTRIBUTOR_CLAIMS), None
    )
    assert result["statusCode"] == 403


# ---------------------------------------------------------------------------
# POST /contributor/presigned-url
# ---------------------------------------------------------------------------


def test_presigned_url_returns_url_and_submission_id(app_module):
    fake_s3 = MagicMock()
    fake_s3.generate_presigned_url.return_value = "https://s3.example/signed"
    with patch.object(app_module.presign, "_s3_client", return_value=fake_s3):
        event = _api_event(
            "POST",
            "/contributor/presigned-url",
            claims=CONTRIBUTOR_CLAIMS,
            body=json.dumps({"fileName": "clip.mp4", "mimeType": "video/mp4"}),
        )
        result = app_module.handler(event, None)
    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["presignedUrl"] == "https://s3.example/signed"
    assert body["bucket"] == "test-submissions-bucket"
    assert body["submissionId"]  # UUID, non-empty
    assert body["s3Key"].startswith(f"staging/{CONTRIBUTOR_CLAIMS['sub']}/")
    assert body["s3Key"].endswith("/clip.mp4")
    # Generated URL should target the submissions bucket with PUT method.
    call = fake_s3.generate_presigned_url.call_args
    assert call.kwargs["ClientMethod"] == "put_object"
    assert call.kwargs["HttpMethod"] == "PUT"
    assert call.kwargs["Params"]["Bucket"] == "test-submissions-bucket"
    assert call.kwargs["Params"]["ContentType"] == "video/mp4"


def test_presigned_url_requires_filename(app_module):
    event = _api_event(
        "POST",
        "/contributor/presigned-url",
        claims=CONTRIBUTOR_CLAIMS,
        body=json.dumps({"mimeType": "video/mp4"}),
    )
    result = app_module.handler(event, None)
    assert result["statusCode"] == 400
    assert "fileName" in json.loads(result["body"])["error"]


def test_presigned_url_rejects_invalid_json(app_module):
    event = _api_event(
        "POST",
        "/contributor/presigned-url",
        claims=CONTRIBUTOR_CLAIMS,
        body="not-json{",
    )
    result = app_module.handler(event, None)
    assert result["statusCode"] == 400
    assert "Invalid JSON" in json.loads(result["body"])["error"]


def test_presigned_url_sanitizes_filename_with_path_separators(app_module):
    fake_s3 = MagicMock()
    fake_s3.generate_presigned_url.return_value = "https://s3.example/signed"
    with patch.object(app_module.presign, "_s3_client", return_value=fake_s3):
        event = _api_event(
            "POST",
            "/contributor/presigned-url",
            claims=CONTRIBUTOR_CLAIMS,
            body=json.dumps({"fileName": "../../etc/passwd"}),
        )
        result = app_module.handler(event, None)
    assert result["statusCode"] == 200
    # basename strips the path, sanitizer leaves the safe basename
    assert json.loads(result["body"])["s3Key"].endswith("/passwd")


# ---------------------------------------------------------------------------
# GET /contributor/submissions
# ---------------------------------------------------------------------------


def test_list_submissions_returns_only_callers_rows(app_module):
    fake_table = MagicMock()
    fake_table.query.return_value = {
        "Items": [
            {
                "submissionId": "s1",
                "userId": "user-abc",
                "fileName": "clip.mp4",
                "uploadedAt": 1700000000,
                "state": "pending-review",
                "art": "aikido",
                "scroll": "ikkajo",
                "rank": "standard",
                "technique": "Technique 5",
                "techniqueCode": "a1505a",
                "variation": "a",
            }
        ],
        "LastEvaluatedKey": None,
    }
    with patch.object(app_module.submissions, "_table", return_value=fake_table):
        result = app_module.handler(
            _api_event("GET", "/contributor/submissions", claims=CONTRIBUTOR_CLAIMS), None
        )
    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert len(body["submissions"]) == 1
    item = body["submissions"][0]
    assert item["submissionId"] == "s1"
    assert item["classification"]["art"] == "aikido"
    assert item["classification"]["techniqueCode"] == "a1505a"
    # Query should be scoped to the caller's sub via the GSI.
    call_kwargs = fake_table.query.call_args.kwargs
    assert call_kwargs["IndexName"] == "userId-submittedAt"
    assert call_kwargs["ExpressionAttributeValues"][":uid"] == "user-abc"


def test_list_submissions_filters_by_state(app_module):
    fake_table = MagicMock()
    fake_table.query.return_value = {"Items": [], "LastEvaluatedKey": None}
    with patch.object(app_module.submissions, "_table", return_value=fake_table):
        result = app_module.handler(
            _api_event(
                "GET",
                "/contributor/submissions",
                claims=CONTRIBUTOR_CLAIMS,
                queryStringParameters={"state": "pending-review"},
            ),
            None,
        )
    assert result["statusCode"] == 200
    call_kwargs = fake_table.query.call_args.kwargs
    assert call_kwargs["FilterExpression"] == "#state = :state"
    assert call_kwargs["ExpressionAttributeValues"][":state"] == "pending-review"


def test_list_submissions_rejects_unknown_state(app_module):
    result = app_module.handler(
        _api_event(
            "GET",
            "/contributor/submissions",
            claims=CONTRIBUTOR_CLAIMS,
            queryStringParameters={"state": "made-up-state"},
        ),
        None,
    )
    assert result["statusCode"] == 400


def test_list_submissions_rejects_bad_limit(app_module):
    result = app_module.handler(
        _api_event(
            "GET",
            "/contributor/submissions",
            claims=CONTRIBUTOR_CLAIMS,
            queryStringParameters={"limit": "not-a-number"},
        ),
        None,
    )
    assert result["statusCode"] == 400


# ---------------------------------------------------------------------------
# POST /contributor/submit-for-review
# ---------------------------------------------------------------------------


_VALID_CLASSIFICATION = {
    "submissionId": "s1",
    "art": "aikido",
    "scroll": "ikkajo",
    "rank": "standard",
    "technique": "Technique 5",
    "techniqueCode": "a1505a",
    "variation": "a",
}


def test_submit_for_review_updates_state(app_module):
    fake_table = MagicMock()
    fake_table.update_item.return_value = {
        "Attributes": {
            "submissionId": "s1",
            "state": "pending-review",
            "submittedForReviewAt": 1700001000,
        }
    }
    with patch.object(app_module.submissions, "_table", return_value=fake_table):
        result = app_module.handler(
            _api_event(
                "POST",
                "/contributor/submit-for-review",
                claims=CONTRIBUTOR_CLAIMS,
                body=json.dumps(_VALID_CLASSIFICATION),
            ),
            None,
        )
    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["state"] == "pending-review"
    # Conditional expression should pin the update to the caller's user id.
    kwargs = fake_table.update_item.call_args.kwargs
    assert kwargs["ExpressionAttributeValues"][":uid"] == "user-abc"
    assert "userId = :uid" in kwargs["ConditionExpression"]


def test_submit_for_review_requires_all_classification_fields(app_module):
    body = {**_VALID_CLASSIFICATION}
    del body["technique"]
    result = app_module.handler(
        _api_event(
            "POST",
            "/contributor/submit-for-review",
            claims=CONTRIBUTOR_CLAIMS,
            body=json.dumps(body),
        ),
        None,
    )
    assert result["statusCode"] == 400
    assert "technique" in json.loads(result["body"])["error"]


def test_submit_for_review_returns_409_on_ownership_violation(app_module):
    from botocore.exceptions import ClientError

    fake_table = MagicMock()
    fake_table.update_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "denied"}},
        "UpdateItem",
    )
    with patch.object(app_module.submissions, "_table", return_value=fake_table):
        result = app_module.handler(
            _api_event(
                "POST",
                "/contributor/submit-for-review",
                claims=CONTRIBUTOR_CLAIMS,
                body=json.dumps(_VALID_CLASSIFICATION),
            ),
            None,
        )
    assert result["statusCode"] == 409


# ---------------------------------------------------------------------------
# S3 ObjectCreated trigger
# ---------------------------------------------------------------------------


def test_s3_event_creates_awaiting_classification_row(app_module):
    fake_table = MagicMock()
    with patch.object(app_module.submissions, "_table", return_value=fake_table):
        app_module.handler(
            _s3_event(
                "test-submissions-bucket",
                "staging/user-abc/submission-uuid-1/clip.mp4",
            ),
            None,
        )
    put = fake_table.put_item.call_args.kwargs
    assert put["Item"]["submissionId"] == "submission-uuid-1"
    assert put["Item"]["userId"] == "user-abc"
    assert put["Item"]["fileName"] == "clip.mp4"
    assert put["Item"]["s3Bucket"] == "test-submissions-bucket"
    assert put["Item"]["state"] == "awaiting-classification"
    assert put["ConditionExpression"] == "attribute_not_exists(submissionId)"


def test_s3_event_ignores_non_staging_keys(app_module):
    fake_table = MagicMock()
    with patch.object(app_module.submissions, "_table", return_value=fake_table):
        app_module.handler(_s3_event("bucket", "logs/random.txt"), None)
    fake_table.put_item.assert_not_called()


def test_s3_event_url_decodes_object_key(app_module):
    fake_table = MagicMock()
    with patch.object(app_module.submissions, "_table", return_value=fake_table):
        app_module.handler(
            _s3_event(
                "test-submissions-bucket",
                "staging/user-abc/sub-1/my%20clip.mp4",
            ),
            None,
        )
    assert fake_table.put_item.call_args.kwargs["Item"]["fileName"] == "my clip.mp4"


def test_s3_event_duplicate_is_idempotent(app_module):
    from botocore.exceptions import ClientError

    fake_table = MagicMock()
    fake_table.put_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
        "PutItem",
    )
    with patch.object(app_module.submissions, "_table", return_value=fake_table):
        # Should not raise: duplicates are silently swallowed.
        app_module.handler(
            _s3_event("test-submissions-bucket", "staging/user-abc/sub-1/clip.mp4"),
            None,
        )


# ---------------------------------------------------------------------------
# Administration stubs land in PR2; verify they return 501 in this PR so
# the dispatcher table is exercised end-to-end.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/administration/submissions"),
        ("POST", "/administration/decide"),
        ("GET", "/administration/submissions/{submissionId}/presigned-preview-url"),
    ],
)
def test_administration_routes_return_501_until_pr2(app_module, method, path):
    event = _api_event(method, path, claims=APPROVER_CLAIMS, body="{}")
    result = app_module.handler(event, None)
    assert result["statusCode"] == 501
