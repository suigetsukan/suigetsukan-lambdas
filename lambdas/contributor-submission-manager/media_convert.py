"""
Hand-off from the contributor staging bucket to the MediaConvert ingest
bucket.

The existing transcoding pipeline already watches
``suigetsukan-approved-for-transcoding`` for newly-uploaded files. Sensei
Mike's "Approve and publish" decision therefore reduces to a single
``copy_object`` from staging to approved with a dense filename — no
MediaConvert API calls, no SNS plumbing, no SQS. The pipeline is wired up
end-to-end already; we just feed it.

Dense filename composition is delegated to the frontend: the contributor
classifies the upload, the frontend computes the canonical stem (e.g.
``a1505``), and the backend appends the variation letter and original
extension. Doing the composition in one place keeps the
art/scroll/rank/technique → filename mapping out of the Lambda — it lives
beside the dropdown data that drove the classification in the first place.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import boto3

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApprovalCopy:
    """Result of copying a submission to the MediaConvert ingest bucket."""

    source_bucket: str
    source_key: str
    target_bucket: str
    target_key: str


def _s3_client():
    region = os.environ.get("AWS_REGION", "us-west-1")
    return boto3.client("s3", region_name=region)


def build_approved_key(technique_code: str, variation: str, original_file_name: str) -> str:
    """Compose the dense filename used by ``file-name-decipher``.

    Inputs come straight from the DynamoDB submission row written during
    submit-for-review. Validation is defensive: any one of these being
    empty / containing path separators is a programming error somewhere
    upstream and we'd rather fail loudly than write a malformed key.
    """
    if not technique_code or "/" in technique_code:
        raise ValueError(f"Invalid techniqueCode: {technique_code!r}")
    if not variation or len(variation) != 1 or not variation.isalpha():
        raise ValueError(f"Invalid variation: {variation!r} (expected a single letter)")
    extension = _extract_extension(original_file_name)
    return f"{technique_code}{variation}{extension}"


def _extract_extension(file_name: str) -> str:
    """Return the dotted extension (``.mp4``), defaulting to ``.mp4``.

    MediaConvert accepts whatever the contributor uploaded, but if the
    original filename arrived without a recognized extension we default
    to mp4 — the StorageManager UI only accepts ``video/*``, and mp4 is
    by far the most common output of mobile recorders.
    """
    base = os.path.basename(file_name or "")
    _, dot, ext = base.rpartition(".")
    if dot and ext and 1 <= len(ext) <= 5 and ext.isalnum():
        return f".{ext.lower()}"
    return ".mp4"


def copy_to_approved_bucket(
    source_bucket: str,
    source_key: str,
    technique_code: str,
    variation: str,
    file_name: str,
) -> ApprovalCopy:
    """Copy the staged video into the approved-for-transcoding bucket.

    The destination key is the dense filename understood by
    ``file-name-decipher``; the existing S3 → MediaConvert pipeline picks
    up the new object and runs from there. We do not delete the source —
    the staging bucket's lifecycle rule sweeps unreferenced uploads after
    30 days, which gives us a recovery window if the operator needs to
    re-run a botched approval.
    """
    target_bucket = os.environ["APPROVED_FOR_TRANSCODING_BUCKET"]
    target_key = build_approved_key(technique_code, variation, file_name)
    _s3_client().copy_object(
        Bucket=target_bucket,
        Key=target_key,
        CopySource={"Bucket": source_bucket, "Key": source_key},
        MetadataDirective="REPLACE",
        Metadata={
            "source-bucket": source_bucket,
            "source-key": source_key,
            "approved-by": "suigetsukan-contributor-submission-manager",
        },
    )
    logger.info(
        "Copied %s/%s → %s/%s",
        source_bucket,
        source_key,
        target_bucket,
        target_key,
    )
    return ApprovalCopy(
        source_bucket=source_bucket,
        source_key=source_key,
        target_bucket=target_bucket,
        target_key=target_key,
    )
