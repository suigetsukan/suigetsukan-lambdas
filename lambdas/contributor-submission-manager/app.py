"""
Contributor submission manager.

Single Lambda fronting:
  - POST  /contributor/presigned-url       — get a presign for upload
  - GET   /contributor/submissions         — list the caller's submissions
  - POST  /contributor/submit-for-review   — promote awaiting → pending
  - GET   /administration/submissions      — moderator queue
  - POST  /administration/decide           — moderator decision
  - GET   /administration/submissions/{submissionId}/presigned-preview-url

…and an S3 ObjectCreated trigger that seeds an ``awaiting-classification``
row in DynamoDB whenever a contributor uploads a video.

Routing approach: API Gateway events carry an ``httpMethod`` key; S3
ObjectCreated events do not — they carry ``Records[].eventSource``. The
dispatcher discriminates on shape and forwards accordingly. This keeps the
two integration paths in one deployment artifact (one role, one set of
env vars, one CI step) while leaving the API surface easy to reason about.

The approval endpoints, MediaConvert hand-off, and SES notifications land
in a follow-up PR (``feat/contributor-backend-approval``). The dispatcher
already routes them through to placeholder handlers so the route table is
visible end-to-end; those handlers return ``501 Not Implemented`` until
the follow-up wires the real logic.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote_plus

from common.constants import (
    COGNITO_GROUP_APPROVER,
    COGNITO_GROUP_CONTRIBUTOR,
    HTTP_BAD_REQUEST,
    HTTP_UNAUTHORIZED,
)

import auth
import presign
import responses
import submissions

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409
HTTP_NOT_IMPLEMENTED = 501
HTTP_SERVER_ERROR = 500

DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 100

# Classification fields required on submit-for-review. ``decisionReason`` is
# specifically NOT required — the contributor side has no notion of it.
_REQUIRED_CLASSIFICATION_FIELDS = (
    "art",
    "scroll",
    "rank",
    "technique",
    "techniqueCode",
    "variation",
)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def handler(event: dict, _context: Any) -> dict | None:
    """Lambda entrypoint. See module docstring for routing details."""
    if _looks_like_s3_event(event):
        return _dispatch_s3(event)
    return _dispatch_api(event)


def _looks_like_s3_event(event: dict) -> bool:
    records = event.get("Records")
    if not isinstance(records, list) or not records:
        return False
    first = records[0]
    return isinstance(first, dict) and first.get("eventSource") == "aws:s3"


# ---------------------------------------------------------------------------
# S3 ObjectCreated handler
# ---------------------------------------------------------------------------


def _dispatch_s3(event: dict) -> None:
    """Create an awaiting-classification row for each new staging upload.

    S3 can deliver ObjectCreated events more than once; ``create_…`` uses
    a conditional put so duplicates are safely ignored. Any other error
    propagates so the event is retried by S3.
    """
    for record in event["Records"]:
        s3 = record.get("s3", {})
        bucket = s3.get("bucket", {}).get("name")
        # Object keys arrive URL-encoded in S3 events; decode before parsing.
        key = unquote_plus(s3.get("object", {}).get("key", ""))
        if not bucket or not key:
            logger.warning("Skipping malformed S3 record: %s", record)
            continue
        _handle_object_created(bucket, key)
    return None


def _handle_object_created(bucket: str, key: str) -> None:
    parsed = presign.parse_staging_key(key)
    if parsed is None:
        logger.info("Ignoring upload outside the staging/ prefix: %s", key)
        return
    user_id, submission_id, file_name = parsed
    item = submissions.NewSubmission(
        submission_id=submission_id,
        user_id=user_id,
        contributor_email=None,
        file_name=file_name,
        s3_key=key,
        s3_bucket=bucket,
    )
    try:
        submissions.create_awaiting_classification(item)
        logger.info(
            "Seeded awaiting-classification row submissionId=%s user=%s",
            submission_id,
            user_id,
        )
    except Exception as exc:  # noqa: BLE001 — surface specific failures below
        if _is_conditional_check_failure(exc):
            logger.info(
                "Duplicate ObjectCreated for submissionId=%s; row already exists",
                submission_id,
            )
            return
        raise


def _is_conditional_check_failure(exc: BaseException) -> bool:
    """True when an exception is a DynamoDB ``ConditionalCheckFailedException``.

    We avoid importing botocore at module load time to keep cold starts
    fast; instead we sniff the exception by name + response code, which
    works whether boto3 raised a typed exception or a generic ClientError.
    """
    response = getattr(exc, "response", {}) or {}
    code = response.get("Error", {}).get("Code")
    return code == "ConditionalCheckFailedException"


# ---------------------------------------------------------------------------
# API Gateway dispatcher
# ---------------------------------------------------------------------------


def _dispatch_api(event: dict) -> dict:
    method = event.get("httpMethod", "")
    path = event.get("resource") or event.get("path", "")

    if method == "OPTIONS":
        return responses.options()

    if auth.is_authentication_required() and not auth.get_user_id(event):
        return responses.error(HTTP_UNAUTHORIZED, "Unauthorized")

    route_key = (method, path)
    handler_fn = _ROUTES.get(route_key)
    if handler_fn is None:
        return responses.error(HTTP_NOT_FOUND, f"No route for {method} {path}")
    try:
        return handler_fn(event)
    except _ApiError as err:
        return responses.error(err.status_code, err.message)
    except KeyError as err:
        return responses.error(HTTP_BAD_REQUEST, f"Missing required field: {err}")
    except ValueError as err:
        return responses.error(HTTP_BAD_REQUEST, str(err))
    except Exception:
        logger.exception("Unhandled error in %s %s", method, path)
        return responses.error(HTTP_SERVER_ERROR, "Internal server error")


class _ApiError(Exception):
    """Carries an HTTP status code so handlers can short-circuit cleanly."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# ---------------------------------------------------------------------------
# Helpers shared across handlers
# ---------------------------------------------------------------------------


def _require_group(event: dict, group: str) -> None:
    if group not in auth.get_groups(event):
        raise _ApiError(HTTP_FORBIDDEN, f"Forbidden: requires {group} group")


def _parse_body(event: dict) -> dict[str, Any]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as err:
        raise _ApiError(HTTP_BAD_REQUEST, f"Invalid JSON body: {err}") from err
    if not isinstance(parsed, dict):
        raise _ApiError(HTTP_BAD_REQUEST, "Body must be a JSON object")
    return parsed


def _list_limit(event: dict) -> int:
    raw = (event.get("queryStringParameters") or {}).get("limit")
    if raw is None:
        return DEFAULT_LIST_LIMIT
    try:
        value = int(raw)
    except (TypeError, ValueError) as err:
        raise _ApiError(HTTP_BAD_REQUEST, "limit must be an integer") from err
    if value < 1 or value > MAX_LIST_LIMIT:
        raise _ApiError(HTTP_BAD_REQUEST, f"limit must be 1..{MAX_LIST_LIMIT}")
    return value


def _next_token_in(event: dict) -> dict | None:
    raw = (event.get("queryStringParameters") or {}).get("nextToken")
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as err:
        raise _ApiError(HTTP_BAD_REQUEST, "nextToken is malformed") from err
    if not isinstance(decoded, dict):
        raise _ApiError(HTTP_BAD_REQUEST, "nextToken must encode a JSON object")
    return decoded


def _next_token_out(last_key: dict | None) -> str | None:
    return json.dumps(last_key, separators=(",", ":"), sort_keys=True) if last_key else None


def _projection_for_contributor(item: dict[str, Any]) -> dict[str, Any]:
    """Trim a raw DynamoDB item to the contributor-facing wire shape."""
    return {
        "submissionId": item.get("submissionId"),
        "fileName": item.get("fileName"),
        "uploadedAt": _coerce_int(item.get("uploadedAt")),
        "classifiedAt": _coerce_int(item.get("classifiedAt")),
        "submittedForReviewAt": _coerce_int(item.get("submittedForReviewAt")),
        "decidedAt": _coerce_int(item.get("decidedAt")),
        "state": item.get("state"),
        "decisionReason": item.get("decisionReason"),
        "classification": _classification_from_item(item),
    }


def _classification_from_item(item: dict[str, Any]) -> dict[str, Any] | None:
    if not item.get("art"):
        return None
    return {
        "art": item.get("art"),
        "scroll": item.get("scroll"),
        "rank": item.get("rank"),
        "technique": item.get("technique"),
        "techniqueCode": item.get("techniqueCode"),
        "variation": item.get("variation"),
    }


def _coerce_int(value: Any) -> int | None:
    """DynamoDB returns Decimal for numbers — coerce to int for JSON."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Contributor handlers
# ---------------------------------------------------------------------------


def _handle_presigned_url(event: dict) -> dict:
    _require_group(event, COGNITO_GROUP_CONTRIBUTOR)
    user_id = auth.get_user_id(event) or ""
    body = _parse_body(event)
    file_name = body.get("fileName")
    if not file_name:
        raise _ApiError(HTTP_BAD_REQUEST, "fileName is required")
    mime_type = body.get("mimeType") or "video/mp4"
    submission_id = submissions.new_submission_id()
    result = presign.generate_upload_url(user_id, submission_id, file_name, mime_type)
    return responses.success(
        {
            "presignedUrl": result.presigned_url,
            "s3Key": result.s3_key,
            "submissionId": result.submission_id,
            "bucket": result.bucket,
        }
    )


def _handle_list_submissions(event: dict) -> dict:
    _require_group(event, COGNITO_GROUP_CONTRIBUTOR)
    user_id = auth.get_user_id(event) or ""
    state = (event.get("queryStringParameters") or {}).get("state")
    if state and state not in submissions.ALL_STATES:
        raise _ApiError(HTTP_BAD_REQUEST, f"Unknown state: {state}")
    items, last_key = submissions.list_for_user(
        user_id, state, _list_limit(event), _next_token_in(event)
    )
    return responses.success(
        {
            "submissions": [_projection_for_contributor(i) for i in items],
            "nextToken": _next_token_out(last_key),
        }
    )


def _handle_submit_for_review(event: dict) -> dict:
    _require_group(event, COGNITO_GROUP_CONTRIBUTOR)
    user_id = auth.get_user_id(event) or ""
    body = _parse_body(event)
    submission_id = body.get("submissionId")
    if not submission_id:
        raise _ApiError(HTTP_BAD_REQUEST, "submissionId is required")
    classification = _extract_classification(body)
    try:
        updated = submissions.submit_for_review(submission_id, user_id, classification)
    except Exception as err:  # noqa: BLE001 — translate to HTTP status
        if _is_conditional_check_failure(err):
            raise _ApiError(
                HTTP_CONFLICT,
                "Submission is not awaiting classification or is owned by another user",
            ) from err
        raise
    return responses.success(
        {
            "submissionId": updated["submissionId"],
            "state": updated["state"],
            "submittedForReviewAt": _coerce_int(updated.get("submittedForReviewAt")),
        }
    )


def _extract_classification(body: dict[str, Any]) -> dict[str, str]:
    """Pull and validate the six classification fields out of the request body."""
    missing = [field for field in _REQUIRED_CLASSIFICATION_FIELDS if not body.get(field)]
    if missing:
        raise _ApiError(
            HTTP_BAD_REQUEST,
            f"Missing classification field(s): {', '.join(missing)}",
        )
    return {field: str(body[field]) for field in _REQUIRED_CLASSIFICATION_FIELDS}


# ---------------------------------------------------------------------------
# Administration handlers — full implementation lands in PR2.
# Stubbed here so the dispatcher table is complete and the public surface
# is visible to anyone reading the file.
# ---------------------------------------------------------------------------


def _handle_list_admin_submissions(event: dict) -> dict:
    _require_group(event, COGNITO_GROUP_APPROVER)
    return responses.error(
        HTTP_NOT_IMPLEMENTED,
        "Administration endpoints land in feat/contributor-backend-approval",
    )


def _handle_decide(event: dict) -> dict:
    _require_group(event, COGNITO_GROUP_APPROVER)
    return responses.error(
        HTTP_NOT_IMPLEMENTED,
        "Administration endpoints land in feat/contributor-backend-approval",
    )


def _handle_preview_url(event: dict) -> dict:
    _require_group(event, COGNITO_GROUP_APPROVER)
    return responses.error(
        HTTP_NOT_IMPLEMENTED,
        "Administration endpoints land in feat/contributor-backend-approval",
    )


# ---------------------------------------------------------------------------
# Route table. Both ``resource`` (path with ``{submissionId}`` placeholder)
# and ``path`` (interpolated path) are accepted — API Gateway sends the
# placeholder form on ``resource`` for proxy integrations.
# ---------------------------------------------------------------------------

_ROUTES: dict[tuple[str, str], Callable[[dict], dict]] = {
    ("POST", "/contributor/presigned-url"): _handle_presigned_url,
    ("GET", "/contributor/submissions"): _handle_list_submissions,
    ("POST", "/contributor/submit-for-review"): _handle_submit_for_review,
    ("GET", "/administration/submissions"): _handle_list_admin_submissions,
    ("POST", "/administration/decide"): _handle_decide,
    (
        "GET",
        "/administration/submissions/{submissionId}/presigned-preview-url",
    ): _handle_preview_url,
}
