"""
DynamoDB operations for the ``contributor_submissions`` table.

Table schema (single-table, no composite key):

  PK = submissionId (string, UUID)

GSIs:
  userId-submittedAt    : PK=userId, SK=submittedForReviewAt
  state-decidedAt       : PK=state,  SK=decidedAt

State machine:
  awaiting-classification → pending-review → approved | declined | changes-requested

Functions here are deliberately thin — each one owns one DynamoDB call so
that the route handlers in ``app.py`` stay easy to test with mocks. None of
the helpers raise on missing rows; callers decide how to surface the
absence.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

import boto3

# State machine constants — kept here (rather than common/) because no
# other lambda touches the submissions table.
STATE_AWAITING_CLASSIFICATION = "awaiting-classification"
STATE_PENDING_REVIEW = "pending-review"
STATE_APPROVED = "approved"
STATE_DECLINED = "declined"
STATE_CHANGES_REQUESTED = "changes-requested"

ALL_STATES = frozenset(
    {
        STATE_AWAITING_CLASSIFICATION,
        STATE_PENDING_REVIEW,
        STATE_APPROVED,
        STATE_DECLINED,
        STATE_CHANGES_REQUESTED,
    }
)

DECISION_STATES = frozenset({STATE_APPROVED, STATE_DECLINED, STATE_CHANGES_REQUESTED})

GSI_USER_SUBMITTED = "userId-submittedAt"
GSI_STATE_DECIDED = "state-decidedAt"


@dataclass(frozen=True)
class NewSubmission:
    """Inputs required to seed an awaiting-classification row from an S3 upload."""

    submission_id: str
    user_id: str
    contributor_email: str | None
    file_name: str
    s3_key: str
    s3_bucket: str


def _table():
    region = os.environ.get("AWS_REGION", "us-west-1")
    table_name = os.environ["CONTRIBUTOR_SUBMISSIONS_TABLE"]
    return boto3.resource("dynamodb", region_name=region).Table(table_name)


def now_seconds() -> int:
    """Unix timestamp at second resolution. Wrapped for test injection."""
    return int(time.time())


def new_submission_id() -> str:
    return str(uuid.uuid4())


def create_awaiting_classification(item: NewSubmission) -> dict[str, Any]:
    """Insert a fresh ``awaiting-classification`` row.

    Uses a conditional put on ``attribute_not_exists(submissionId)`` so a
    duplicate S3 ObjectCreated event (S3 can deliver more than once) doesn't
    overwrite an in-flight classification.
    """
    record: dict[str, Any] = {
        "submissionId": item.submission_id,
        "userId": item.user_id,
        "fileName": item.file_name,
        "s3Key": item.s3_key,
        "s3Bucket": item.s3_bucket,
        "uploadedAt": now_seconds(),
        "state": STATE_AWAITING_CLASSIFICATION,
    }
    if item.contributor_email:
        record["contributorEmail"] = item.contributor_email
    _table().put_item(
        Item=record,
        ConditionExpression="attribute_not_exists(submissionId)",
    )
    return record


def get(submission_id: str) -> dict[str, Any] | None:
    response = _table().get_item(Key={"submissionId": submission_id})
    return response.get("Item")


def submit_for_review(
    submission_id: str,
    user_id: str,
    classification: dict[str, str],
) -> dict[str, Any]:
    """Move a row from awaiting-classification to pending-review.

    Returns the updated item. Conditional on ownership (userId match) and
    on the current state being awaiting-classification or changes-requested
    — the latter allows a contributor to resubmit after feedback.
    """
    now = now_seconds()
    fields = {
        "art": classification["art"],
        "scroll": classification["scroll"],
        "rank": classification["rank"],
        "technique": classification["technique"],
        "techniqueCode": classification["techniqueCode"],
        "variation": classification["variation"],
        "classifiedAt": now,
        "submittedForReviewAt": now,
        "state": STATE_PENDING_REVIEW,
    }
    update_expr, names, values = _build_set_expression(fields)
    values[":uid"] = user_id
    values[":s_awaiting"] = STATE_AWAITING_CLASSIFICATION
    values[":s_changes"] = STATE_CHANGES_REQUESTED
    response = _table().update_item(
        Key={"submissionId": submission_id},
        UpdateExpression=update_expr,
        ConditionExpression=("userId = :uid AND (#state = :s_awaiting OR #state = :s_changes)"),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )
    return response["Attributes"]


def _build_set_expression(
    fields: dict[str, Any],
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Compose an UpdateExpression that sets every entry in ``fields``.

    Each key is referenced via ``#attr_<n>`` to dodge reserved-word
    collisions (``state``, ``rank`` are both reserved by DynamoDB).
    """
    parts: list[str] = []
    names: dict[str, str] = {}
    values: dict[str, Any] = {}
    for idx, (key, value) in enumerate(fields.items()):
        name_alias = f"#a{idx}"
        value_alias = f":v{idx}"
        names[name_alias] = key
        values[value_alias] = value
        parts.append(f"{name_alias} = {value_alias}")
    # ``#state`` is reused by the ConditionExpression in submit_for_review
    # and decide() — alias it explicitly so both clauses share one name.
    names["#state"] = "state"
    return "SET " + ", ".join(parts), names, values


def list_for_user(
    user_id: str,
    state: str | None,
    limit: int,
    next_token: dict | None = None,
) -> tuple[list[dict], dict | None]:
    """Query the userId-submittedAt GSI for a contributor's submissions."""
    kwargs: dict[str, Any] = {
        "IndexName": GSI_USER_SUBMITTED,
        "KeyConditionExpression": "userId = :uid",
        "ExpressionAttributeValues": {":uid": user_id},
        "ScanIndexForward": False,
        "Limit": limit,
    }
    if state:
        kwargs["FilterExpression"] = "#state = :state"
        kwargs["ExpressionAttributeNames"] = {"#state": "state"}
        kwargs["ExpressionAttributeValues"][":state"] = state
    if next_token:
        kwargs["ExclusiveStartKey"] = next_token
    response = _table().query(**kwargs)
    return response.get("Items", []), response.get("LastEvaluatedKey")


def list_by_state(
    state: str,
    limit: int,
    next_token: dict | None = None,
) -> tuple[list[dict], dict | None]:
    """Query the state-decidedAt GSI for moderator-visible submissions."""
    kwargs: dict[str, Any] = {
        "IndexName": GSI_STATE_DECIDED,
        "KeyConditionExpression": "#state = :state",
        "ExpressionAttributeNames": {"#state": "state"},
        "ExpressionAttributeValues": {":state": state},
        "ScanIndexForward": False,
        "Limit": limit,
    }
    if next_token:
        kwargs["ExclusiveStartKey"] = next_token
    response = _table().query(**kwargs)
    return response.get("Items", []), response.get("LastEvaluatedKey")


def record_decision(
    submission_id: str,
    decision_state: str,
    decided_by: str,
    decision_reason: str | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update a submission with a moderator decision.

    Conditional on the current state being ``pending-review`` so a stale
    request from a reviewer's stale page can't overwrite a fresh decision.
    """
    if decision_state not in DECISION_STATES:
        raise ValueError(f"Invalid decision state: {decision_state}")
    fields: dict[str, Any] = {
        "state": decision_state,
        "decidedAt": now_seconds(),
        "decisionBy": decided_by,
    }
    if decision_reason:
        fields["decisionReason"] = decision_reason
    if extra:
        fields.update(extra)
    update_expr, names, values = _build_set_expression(fields)
    values[":s_pending"] = STATE_PENDING_REVIEW
    response = _table().update_item(
        Key={"submissionId": submission_id},
        UpdateExpression=update_expr,
        ConditionExpression="#state = :s_pending",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )
    return response["Attributes"]
