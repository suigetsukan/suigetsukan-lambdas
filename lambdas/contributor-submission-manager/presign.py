"""
S3 helpers: presigned URLs for upload and preview, plus key parsing for
the ObjectCreated event handler.

Object naming convention enforced everywhere:

    staging/{userId}/{submissionId}/{fileName}

The path is single-source-of-truth for tying an S3 object back to its
owning user and submission record — both the upload presign and the
ObjectCreated event read it the same way.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

import boto3

# RFC 3986 + S3 key safety: alphanumerics, dash, dot, underscore. Anything
# else gets URL-encoded so we don't break the presign or the consumer.
_SAFE_FILE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,255}$")

UPLOAD_URL_TTL_SECONDS = 15 * 60  # 15 minutes — long enough for a single video
PREVIEW_URL_TTL_SECONDS = 60 * 60  # 1 hour — Sensei Mike's review window


@dataclass(frozen=True)
class UploadPresign:
    """Result of generating an upload URL for a fresh submission."""

    presigned_url: str
    s3_key: str
    submission_id: str
    bucket: str


def _s3_client():
    region = os.environ.get("AWS_REGION", "us-west-1")
    # SigV4 is required for ``s3:PutObject`` presigns in regions other than
    # us-east-1; specifying it explicitly avoids the SDK falling back to
    # SigV2 on older boto versions.
    return boto3.client(
        "s3",
        region_name=region,
        config=boto3.session.Config(signature_version="s3v4"),
    )


def sanitize_file_name(file_name: str) -> str:
    """Ensure the uploaded file name is safe to splice into an S3 key.

    Strips path separators and reserved characters, then re-checks against
    the allow-list. Returns a normalized name suitable to store both as
    the object's path suffix and as the ``fileName`` DynamoDB attribute.
    """
    base = os.path.basename(file_name or "").strip()
    if not base:
        raise ValueError("File name is required")
    if _SAFE_FILE_NAME.match(base):
        return base
    # Fallback: URL-encode anything we don't trust. This keeps the original
    # name visible (good for debugging and the contributor's UI) without
    # breaking the S3 key.
    return urllib.parse.quote(base, safe="._-")


def build_staging_key(user_id: str, submission_id: str, file_name: str) -> str:
    safe_name = sanitize_file_name(file_name)
    return f"staging/{user_id}/{submission_id}/{safe_name}"


def parse_staging_key(key: str) -> tuple[str, str, str] | None:
    """Return ``(userId, submissionId, fileName)`` from a staging key.

    Returns ``None`` when the key does not match the expected layout — the
    ObjectCreated handler uses that to ignore stray uploads (e.g., a test
    object placed at the bucket root by an operator).
    """
    parts = key.split("/", 3)
    if len(parts) != 4 or parts[0] != "staging":
        return None
    user_id, submission_id, file_name = parts[1], parts[2], parts[3]
    if not user_id or not submission_id or not file_name:
        return None
    return user_id, submission_id, file_name


def generate_upload_url(
    user_id: str,
    submission_id: str,
    file_name: str,
    mime_type: str,
) -> UploadPresign:
    """Generate a PUT presigned URL scoped to this user's staging prefix."""
    bucket = os.environ["CONTRIBUTOR_SUBMISSIONS_BUCKET"]
    key = build_staging_key(user_id, submission_id, file_name)
    params: dict[str, Any] = {"Bucket": bucket, "Key": key}
    if mime_type:
        params["ContentType"] = mime_type
    url = _s3_client().generate_presigned_url(
        ClientMethod="put_object",
        Params=params,
        ExpiresIn=UPLOAD_URL_TTL_SECONDS,
        HttpMethod="PUT",
    )
    return UploadPresign(
        presigned_url=url,
        s3_key=key,
        submission_id=submission_id,
        bucket=bucket,
    )


def generate_preview_url(bucket: str, key: str) -> str:
    """GET presigned URL for a moderator to watch a pending submission."""
    return _s3_client().generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=PREVIEW_URL_TTL_SECONDS,
        HttpMethod="GET",
    )
