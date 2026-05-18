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
    "media_convert",
    "notifications",
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
# GET /administration/submissions
# ---------------------------------------------------------------------------


def _pending_item(submission_id: str = "p1") -> dict:
    """A fully-populated DDB row in state=pending-review for admin tests."""
    return {
        "submissionId": submission_id,
        "userId": "user-abc",
        "contributorEmail": "yamamoto@example.com",
        "fileName": "clip.mp4",
        "s3Key": f"staging/user-abc/{submission_id}/clip.mp4",
        "s3Bucket": "test-submissions-bucket",
        "uploadedAt": 1700000000,
        "submittedForReviewAt": 1700001000,
        "state": "pending-review",
        "art": "aikido",
        "scroll": "ikkajo",
        "rank": "standard",
        "technique": "Technique 5",
        "techniqueCode": "a1505",
        "variation": "a",
    }


def test_list_admin_submissions_defaults_to_pending_review(app_module):
    fake_table = MagicMock()
    fake_table.query.return_value = {"Items": [_pending_item()], "LastEvaluatedKey": None}
    with patch.object(app_module.submissions, "_table", return_value=fake_table):
        result = app_module.handler(
            _api_event("GET", "/administration/submissions", claims=APPROVER_CLAIMS), None
        )
    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert len(body["submissions"]) == 1
    item = body["submissions"][0]
    # Admin projection exposes the moderator-only fields the contributor
    # projection hides.
    assert item["contributorEmail"] == "yamamoto@example.com"
    assert item["s3Bucket"] == "test-submissions-bucket"
    assert item["s3Key"].startswith("staging/")
    kwargs = fake_table.query.call_args.kwargs
    assert kwargs["IndexName"] == "state-decidedAt"
    assert kwargs["ExpressionAttributeValues"][":state"] == "pending-review"


def test_list_admin_submissions_filters_by_explicit_state(app_module):
    fake_table = MagicMock()
    fake_table.query.return_value = {"Items": [], "LastEvaluatedKey": None}
    with patch.object(app_module.submissions, "_table", return_value=fake_table):
        result = app_module.handler(
            _api_event(
                "GET",
                "/administration/submissions",
                claims=APPROVER_CLAIMS,
                queryStringParameters={"state": "approved"},
            ),
            None,
        )
    assert result["statusCode"] == 200
    assert fake_table.query.call_args.kwargs["ExpressionAttributeValues"][":state"] == "approved"


def test_list_admin_submissions_rejects_unknown_state(app_module):
    result = app_module.handler(
        _api_event(
            "GET",
            "/administration/submissions",
            claims=APPROVER_CLAIMS,
            queryStringParameters={"state": "nonsense"},
        ),
        None,
    )
    assert result["statusCode"] == 400


# ---------------------------------------------------------------------------
# POST /administration/decide
# ---------------------------------------------------------------------------


def test_decide_approved_copies_to_mediaconvert_and_records(app_module):
    """Happy path: approve a pending submission.

    DynamoDB get_item returns the pending row; S3 copy_object succeeds;
    update_item flips state to approved and records mediaConvertKey.
    SES send_email is best-effort and asserted to have been called.
    """
    pending = _pending_item("approve-1")
    fake_table = MagicMock()
    fake_table.get_item.return_value = {"Item": pending}
    fake_table.update_item.return_value = {
        "Attributes": {
            **pending,
            "state": "approved",
            "decidedAt": 1700002000,
            "mediaConvertKey": "a1505a.mp4",
        }
    }
    fake_s3 = MagicMock()
    fake_ses = MagicMock()
    fake_ses.send_email.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
    with (
        patch.object(app_module.submissions, "_table", return_value=fake_table),
        patch.object(app_module.media_convert, "_s3_client", return_value=fake_s3),
        patch.object(app_module.notifications, "_ses_client", return_value=fake_ses),
    ):
        result = app_module.handler(
            _api_event(
                "POST",
                "/administration/decide",
                claims=APPROVER_CLAIMS,
                body=json.dumps(
                    {
                        "submissionId": "approve-1",
                        "decision": "approved",
                        "reason": "Looks good",
                    }
                ),
            ),
            None,
        )
    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["state"] == "approved"
    assert body["mediaConvertKey"] == "a1505a.mp4"

    # Verify the file landed in the approved bucket with the dense name.
    copy_kwargs = fake_s3.copy_object.call_args.kwargs
    assert copy_kwargs["Bucket"] == "test-approved-bucket"
    assert copy_kwargs["Key"] == "a1505a.mp4"
    assert copy_kwargs["CopySource"] == {
        "Bucket": "test-submissions-bucket",
        "Key": "staging/user-abc/approve-1/clip.mp4",
    }
    # Verify the SES send was attempted with the contributor's address.
    assert fake_ses.send_email.called
    send_kwargs = fake_ses.send_email.call_args.kwargs
    assert send_kwargs["Destination"]["ToAddresses"] == ["yamamoto@example.com"]
    assert "approved" in send_kwargs["Message"]["Subject"]["Data"].lower()


def test_decide_declined_skips_copy_and_records_reason(app_module):
    pending = _pending_item("decline-1")
    fake_table = MagicMock()
    fake_table.get_item.return_value = {"Item": pending}
    fake_table.update_item.return_value = {
        "Attributes": {
            **pending,
            "state": "declined",
            "decidedAt": 1700002500,
            "decisionReason": "Camera was out of focus",
        }
    }
    fake_s3 = MagicMock()
    fake_ses = MagicMock()
    fake_ses.send_email.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
    with (
        patch.object(app_module.submissions, "_table", return_value=fake_table),
        patch.object(app_module.media_convert, "_s3_client", return_value=fake_s3),
        patch.object(app_module.notifications, "_ses_client", return_value=fake_ses),
    ):
        result = app_module.handler(
            _api_event(
                "POST",
                "/administration/decide",
                claims=APPROVER_CLAIMS,
                body=json.dumps(
                    {
                        "submissionId": "decline-1",
                        "decision": "declined",
                        "reason": "Camera was out of focus",
                    }
                ),
            ),
            None,
        )
    assert result["statusCode"] == 200
    assert json.loads(result["body"])["state"] == "declined"
    fake_s3.copy_object.assert_not_called()
    # SES still fires — the contributor learns about the decline.
    assert fake_ses.send_email.called
    assert "feedback" in fake_ses.send_email.call_args.kwargs["Message"]["Subject"]["Data"].lower()


def test_decide_changes_requested_skips_copy(app_module):
    pending = _pending_item("changes-1")
    fake_table = MagicMock()
    fake_table.get_item.return_value = {"Item": pending}
    fake_table.update_item.return_value = {
        "Attributes": {**pending, "state": "changes-requested", "decidedAt": 1700003000},
    }
    fake_s3 = MagicMock()
    fake_ses = MagicMock()
    fake_ses.send_email.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
    with (
        patch.object(app_module.submissions, "_table", return_value=fake_table),
        patch.object(app_module.media_convert, "_s3_client", return_value=fake_s3),
        patch.object(app_module.notifications, "_ses_client", return_value=fake_ses),
    ):
        result = app_module.handler(
            _api_event(
                "POST",
                "/administration/decide",
                claims=APPROVER_CLAIMS,
                body=json.dumps(
                    {
                        "submissionId": "changes-1",
                        "decision": "changes-requested",
                        "reason": "Try once more with the bokken oriented forward",
                    }
                ),
            ),
            None,
        )
    assert result["statusCode"] == 200
    assert json.loads(result["body"])["state"] == "changes-requested"
    fake_s3.copy_object.assert_not_called()


def test_decide_rejects_unknown_decision(app_module):
    result = app_module.handler(
        _api_event(
            "POST",
            "/administration/decide",
            claims=APPROVER_CLAIMS,
            body=json.dumps({"submissionId": "x", "decision": "maybe"}),
        ),
        None,
    )
    assert result["statusCode"] == 400


def test_decide_returns_404_when_submission_missing(app_module):
    fake_table = MagicMock()
    fake_table.get_item.return_value = {}
    with patch.object(app_module.submissions, "_table", return_value=fake_table):
        result = app_module.handler(
            _api_event(
                "POST",
                "/administration/decide",
                claims=APPROVER_CLAIMS,
                body=json.dumps({"submissionId": "nope", "decision": "approved"}),
            ),
            None,
        )
    assert result["statusCode"] == 404


def test_decide_returns_409_when_not_pending_review(app_module):
    already_approved = {**_pending_item("p1"), "state": "approved"}
    fake_table = MagicMock()
    fake_table.get_item.return_value = {"Item": already_approved}
    with patch.object(app_module.submissions, "_table", return_value=fake_table):
        result = app_module.handler(
            _api_event(
                "POST",
                "/administration/decide",
                claims=APPROVER_CLAIMS,
                body=json.dumps({"submissionId": "p1", "decision": "approved"}),
            ),
            None,
        )
    assert result["statusCode"] == 409


def test_decide_returns_400_when_approval_required_fields_missing(app_module):
    incomplete = _pending_item("nocode")
    incomplete["techniqueCode"] = ""
    fake_table = MagicMock()
    fake_table.get_item.return_value = {"Item": incomplete}
    with patch.object(app_module.submissions, "_table", return_value=fake_table):
        result = app_module.handler(
            _api_event(
                "POST",
                "/administration/decide",
                claims=APPROVER_CLAIMS,
                body=json.dumps({"submissionId": "nocode", "decision": "approved"}),
            ),
            None,
        )
    assert result["statusCode"] == 400
    assert "techniqueCode" in json.loads(result["body"])["error"]


def test_decide_does_not_block_on_ses_failure(app_module):
    """A failed SES send must NOT fail the decision API response.

    The decision is durable in DynamoDB before the email goes out, so we
    log the failure and return 200 — the moderator's action persisted.
    """
    pending = _pending_item("ses-fail")
    fake_table = MagicMock()
    fake_table.get_item.return_value = {"Item": pending}
    fake_table.update_item.return_value = {
        "Attributes": {**pending, "state": "declined", "decidedAt": 1700004000},
    }
    fake_ses = MagicMock()
    fake_ses.send_email.side_effect = RuntimeError("SES is down")
    with (
        patch.object(app_module.submissions, "_table", return_value=fake_table),
        patch.object(app_module.notifications, "_ses_client", return_value=fake_ses),
    ):
        result = app_module.handler(
            _api_event(
                "POST",
                "/administration/decide",
                claims=APPROVER_CLAIMS,
                body=json.dumps(
                    {
                        "submissionId": "ses-fail",
                        "decision": "declined",
                        "reason": "audio is choppy",
                    }
                ),
            ),
            None,
        )
    assert result["statusCode"] == 200


# ---------------------------------------------------------------------------
# GET /administration/submissions/{submissionId}/presigned-preview-url
# ---------------------------------------------------------------------------


def test_preview_url_returns_signed_get(app_module):
    item = _pending_item("preview-1")
    fake_table = MagicMock()
    fake_table.get_item.return_value = {"Item": item}
    fake_s3 = MagicMock()
    fake_s3.generate_presigned_url.return_value = "https://s3.example/get?sig=x"
    with (
        patch.object(app_module.submissions, "_table", return_value=fake_table),
        patch.object(app_module.presign, "_s3_client", return_value=fake_s3),
    ):
        result = app_module.handler(
            _api_event(
                "GET",
                "/administration/submissions/{submissionId}/presigned-preview-url",
                claims=APPROVER_CLAIMS,
                pathParameters={"submissionId": "preview-1"},
            ),
            None,
        )
    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["presignedUrl"] == "https://s3.example/get?sig=x"
    assert body["submissionId"] == "preview-1"
    assert body["expiresInSeconds"] == 60 * 60
    call = fake_s3.generate_presigned_url.call_args
    assert call.kwargs["ClientMethod"] == "get_object"
    assert call.kwargs["HttpMethod"] == "GET"


def test_preview_url_returns_404_when_submission_missing(app_module):
    fake_table = MagicMock()
    fake_table.get_item.return_value = {}
    with patch.object(app_module.submissions, "_table", return_value=fake_table):
        result = app_module.handler(
            _api_event(
                "GET",
                "/administration/submissions/{submissionId}/presigned-preview-url",
                claims=APPROVER_CLAIMS,
                pathParameters={"submissionId": "missing"},
            ),
            None,
        )
    assert result["statusCode"] == 404


# ---------------------------------------------------------------------------
# media_convert.build_approved_key — unit tests for the filename composer
# ---------------------------------------------------------------------------


def test_build_approved_key_composes_dense_filename(app_module):
    assert app_module.media_convert.build_approved_key("a1505", "a", "clip.mp4") == "a1505a.mp4"


def test_build_approved_key_preserves_original_extension(app_module):
    assert app_module.media_convert.build_approved_key("c01", "b", "recording.MOV") == "c01b.mov"


def test_build_approved_key_defaults_to_mp4_when_extension_unknown(app_module):
    assert app_module.media_convert.build_approved_key("a1505", "a", "noextension") == "a1505a.mp4"


def test_build_approved_key_rejects_invalid_variation(app_module):
    with pytest.raises(ValueError):
        app_module.media_convert.build_approved_key("a1505", "AA", "clip.mp4")
    with pytest.raises(ValueError):
        app_module.media_convert.build_approved_key("a1505", "1", "clip.mp4")
