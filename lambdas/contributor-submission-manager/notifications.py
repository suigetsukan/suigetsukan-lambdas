"""
SES notifications for contributor submissions.

Two templates today, both plain-text:

  - approved        → "Your recording was approved"
  - changes/declined → "Sensei Mike has feedback on your recording"

The templates intentionally avoid HTML — the contributor inbox is varied
(Gmail / iCloud / dojo-hosted), and a plaintext message renders identically
everywhere with no engagement-tracking weirdness. If we add HTML later it
goes alongside the plaintext as a SES ``Body.Html`` alternative.

Email is best-effort: ``send_decision_email`` swallows SES errors and
returns whether the send actually succeeded. The caller logs but does not
fail the API request — failing the approval API just because SES had a
hiccup would be a poor trade.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import boto3

from common.constants import CHARSET_UTF8

from submissions import (
    STATE_APPROVED,
    STATE_CHANGES_REQUESTED,
    STATE_DECLINED,
)

logger = logging.getLogger(__name__)


def _ses_client():
    region = os.environ.get("SES_REGION", os.environ.get("AWS_REGION", "us-west-1"))
    return boto3.client("ses", region_name=region)


def send_decision_email(submission: dict[str, Any]) -> bool:
    """Send the right template for the submission's state.

    Returns True on success, False on any kind of failure (missing email,
    SES error, unsupported state). Callers should not block the API
    response on this — the email is a follow-up, not the decision itself.
    """
    email = submission.get("contributorEmail")
    state = submission.get("state")
    if not email:
        logger.info(
            "Skipping decision email: no contributorEmail on submission %s",
            submission.get("submissionId"),
        )
        return False
    template = _select_template(state)
    if template is None:
        logger.warning("No email template for state=%s", state)
        return False
    subject, body = template(submission)
    try:
        _send(email, subject, body)
        return True
    except Exception:  # noqa: BLE001 — best-effort, log everything
        logger.exception(
            "SES send failed for submission %s state=%s",
            submission.get("submissionId"),
            state,
        )
        return False


def _select_template(state: str | None):
    if state == STATE_APPROVED:
        return _approved_template
    if state in (STATE_DECLINED, STATE_CHANGES_REQUESTED):
        return _feedback_template
    return None


def _classification_phrase(submission: dict[str, Any]) -> str:
    parts = [
        submission.get("art"),
        submission.get("scroll"),
        submission.get("technique"),
    ]
    parts = [p for p in parts if p]
    description = " — ".join(parts) if parts else "your recording"
    variation = submission.get("variation")
    if variation:
        description = f"{description} ({variation})"
    return description


def _approved_template(submission: dict[str, Any]) -> tuple[str, str]:
    subject = "Your Suigetsukan recording was approved"
    body_lines = [
        "Hi,",
        "",
        (
            f"Great news — {_classification_phrase(submission)} has been approved "
            "and is now being processed for publication."
        ),
        "",
        "It will appear on the Suigetsukan curriculum site within about 24 hours.",
        "",
        "Thank you for contributing.",
        "",
        "— Suigetsukan",
    ]
    site_url = os.environ.get("CURRICULUM_SITE_URL")
    if site_url:
        body_lines.insert(-2, f"You can visit the site here: {site_url}")
    return subject, "\n".join(body_lines)


def _feedback_template(submission: dict[str, Any]) -> tuple[str, str]:
    state = submission.get("state")
    if state == STATE_DECLINED:
        subject = "Feedback on your Suigetsukan recording"
        opener = (
            f"Sensei Mike has reviewed {_classification_phrase(submission)} "
            "and has declined this submission for the following reason:"
        )
    else:
        subject = "Changes requested on your Suigetsukan recording"
        opener = (
            f"Sensei Mike has reviewed {_classification_phrase(submission)} "
            "and would like a few adjustments before publishing:"
        )
    reason = submission.get("decisionReason") or (
        "No additional note was provided. Please reach out to Sensei Mike "
        "directly if you would like more detail."
    )
    body_lines = [
        "Hi,",
        "",
        opener,
        "",
        f"    {reason}",
        "",
        "When you're ready, you can re-record and resubmit from the contributor portal.",
        "",
        "— Suigetsukan",
    ]
    return subject, "\n".join(body_lines)


def _send(to_address: str, subject: str, body: str) -> None:
    source = os.environ["AWS_SES_SOURCE_EMAIL"]
    response = _ses_client().send_email(
        Destination={"ToAddresses": [to_address]},
        Message={
            "Body": {"Text": {"Charset": CHARSET_UTF8, "Data": body}},
            "Subject": {"Charset": CHARSET_UTF8, "Data": subject},
        },
        Source=source,
    )
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    if status != 200:
        raise RuntimeError(f"SES returned HTTP {status}")
