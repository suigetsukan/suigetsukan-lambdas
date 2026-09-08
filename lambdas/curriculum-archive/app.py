"""
Curriculum archive Lambda.

Monthly catastrophic-failure backup of the Suigetsukan curriculum. Writes, to a
versioned / Object-Locked bucket in a *different* region from the running system:

  (a) every source video master           -> videos/<stem>.mov
  (b) the three curriculum DynamoDB tables -> snapshots/<date>/tables/<art>.json.gz
  (c) a flat manifest mapping every video to art / scroll / technique / variation
                                           -> snapshots/<date>/manifest.{csv,json}
  (d) the documentation needed to read it  -> snapshots/<date>/docs/, VERSION

`snapshots/latest/` is overwritten every run and is the pointer used by RESTORE.md.

Steps, in order (each is its own function so the handler stays simple):
  1. export_tables       full paginated scan of each table, Decimal -> int/float
  2. build_manifest      join table variations with the source-bucket listing
  3. copy_docs           reference doc, mapping modules, RESTORE.md, VERSION
  4. sync_videos         cross-region server-side copy of new / changed masters,
                         stops with 60 s left and lets the next run continue
  5. verify + notify     head_object on manifest + snapshots, SNS summary;
                         any exception -> SNS failure message, re-raise

Environment variables:
- AWS_DDB_AIKIDO_TABLE_NAME, AWS_DDB_BATTODO_TABLE_NAME, AWS_DDB_DANZAN_RYU_TABLE_NAME
- SOURCE_VIDEO_BUCKET    bucket in AWS_REGION holding <stem>.mov masters
- ARCHIVE_BUCKET         archive bucket (infra/archive-bucket.yaml)
- ARCHIVE_BUCKET_REGION  region of ARCHIVE_BUCKET (default us-west-2)
- SNS_SUPPORT_TOPIC_ARN  optional; success and failure summaries
- DEPLOYED_GIT_SHA       optional; set by the pipeline, recorded in VERSION
"""

import base64
import csv
import gzip
import hashlib
import io
import json
import logging
import os
import re
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError

from common.constants import DDB_ITEMS_KEY, DDB_MAP_KEY, DDB_VARIATIONS_KEY, DEFAULT_REGION

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DEFAULT_ARCHIVE_REGION = "us-west-2"
SNAPSHOT_PREFIX = "snapshots"
LATEST_PREFIX = f"{SNAPSHOT_PREFIX}/latest"
VIDEO_PREFIX = "videos"
DOCS_SUBDIR = "docs"
TABLES_SUBDIR = "tables"
VERSION_FILE = "VERSION"

# Only keys shaped like a real master are archived; anything else (e.g. `bi01ca.movv`)
# is reported as junk in the summary and never copied.
VIDEO_KEY_PATTERN = re.compile(r"^[abd][a-z0-9]+\.mov$")
VIDEO_CONTENT_TYPE = "video/quicktime"

# Stop starting new copies when this much Lambda time remains; the next run continues.
TIME_RESERVE_MS = 60_000
# Remaining-time value used when no Lambda context is supplied (local runs / tests).
NO_CONTEXT_REMAINING_MS = 900_000
# Cap on per-key lists embedded in the SNS summary so the message stays readable.
MAX_LISTED_KEYS = 50

META_SOURCE_ETAG = "source-etag"
META_SOURCE_LAST_MODIFIED = "source-last-modified"
META_STEM = "stem"

STATUS_MAPPED = "mapped"
STATUS_MISSING_SOURCE = "missing_source"
STATUS_ORPHAN_SOURCE = "orphan_source"

DDB_SCROLL_NAME_KEY = "Name"
DDB_TECHNIQUE_NUMBER_KEY = "Number"
# Aikido and Danzan Ryu carry the technique display name in `Name`; Battodo uses `Techniques`.
TECHNIQUE_NAME_KEYS = ("Name", "Techniques")

ART_TABLE_ENV = {
    "aikido": "AWS_DDB_AIKIDO_TABLE_NAME",
    "battodo": "AWS_DDB_BATTODO_TABLE_NAME",
    "danzan_ryu": "AWS_DDB_DANZAN_RYU_TABLE_NAME",
}
ART_BY_STEM_PREFIX = {"a": "aikido", "b": "battodo", "d": "danzan_ryu"}

MANIFEST_COLUMNS = [
    "art",
    "scroll",
    "technique_index",
    "technique_number",
    "technique_name",
    "variation_index",
    "stem",
    "hls_url",
    "source_key",
    "source_size_bytes",
    "source_etag",
    "source_last_modified",
    "status",
]

# Files uploaded to snapshots/<date>/docs/, relative to the deployed code directory.
# `bundle/` is populated at deploy time from config.json `bundle_files`; `common/` is
# copied into every Lambda package by deploy_lambdas.py; RESTORE.md lives beside app.py.
DOC_FILES = (
    "bundle/TECHNIQUE_TO_FILENAME_REFERENCE.md",
    "common/aikido_mappings.py",
    "common/battodo_mappings.py",
    "common/danzan_ryu_mappings.py",
    "RESTORE.md",
)
DOC_CONTENT_TYPES = {".md": "text/markdown", ".py": "text/x-python"}
DEFAULT_TEXT_CONTENT_TYPE = "text/plain"

S3_NOT_FOUND_CODES = ("404", "NoSuchKey", "NotFound")

# Directory holding app.py inside the deployment package (patched in tests).
CODE_DIR = Path(__file__).resolve().parent

SUBJECT_SUCCESS = "Curriculum Archive OK"
SUBJECT_FAILURE = "Curriculum Archive FAILURE"


@dataclass(frozen=True)
class ArchiveSettings:
    """Resolved environment configuration for one run."""

    tables: dict[str, str]
    source_bucket: str
    archive_bucket: str
    archive_region: str
    sns_topic_arn: str | None
    git_sha: str | None


@dataclass(frozen=True)
class SourceObject:
    """One master video in the source bucket."""

    key: str
    stem: str
    size: int
    etag: str
    last_modified: str


@dataclass(frozen=True)
class Variation:
    """One (technique, variation) pair read from a curriculum table."""

    art: str
    scroll: str
    technique_index: int
    technique_number: str
    technique_name: str
    variation_index: int
    hls_url: str


@dataclass(frozen=True)
class Snapshot:
    """Where this run writes and when it started."""

    date_prefix: str
    timestamp: str


@dataclass
class SyncResult:
    """Outcome of the video sync step."""

    copied: list[str] = field(default_factory=list)
    unchanged: int = 0
    skipped_for_time: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Settings and clients
# ---------------------------------------------------------------------------


def _require_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise ValueError(f"{name} must be set")
    return value


def load_settings() -> ArchiveSettings:
    """Read and validate the environment; raises ValueError on a missing required var."""
    return ArchiveSettings(
        tables={art: _require_env(env_name) for art, env_name in ART_TABLE_ENV.items()},
        source_bucket=_require_env("SOURCE_VIDEO_BUCKET"),
        archive_bucket=_require_env("ARCHIVE_BUCKET"),
        archive_region=(os.environ.get("ARCHIVE_BUCKET_REGION") or "").strip()
        or DEFAULT_ARCHIVE_REGION,
        sns_topic_arn=(os.environ.get("SNS_SUPPORT_TOPIC_ARN") or "").strip() or None,
        git_sha=(os.environ.get("DEPLOYED_GIT_SHA") or "").strip() or None,
    )


def _get_clients(archive_region: str) -> dict[str, Any]:
    """Source-side clients use the Lambda's own region; the archive client uses its bucket's."""
    source_region = os.environ.get("AWS_REGION", DEFAULT_REGION)
    return {
        "dynamodb": boto3.client("dynamodb", region_name=source_region),
        "s3_source": boto3.client("s3", region_name=source_region),
        "s3_archive": boto3.client("s3", region_name=archive_region),
        "sns": boto3.client("sns", region_name=source_region),
    }


def _remaining_ms_fn(context: Any) -> Callable[[], int]:
    getter = getattr(context, "get_remaining_time_in_millis", None)
    if not callable(getter):
        return lambda: NO_CONTEXT_REMAINING_MS
    return lambda: int(getter())


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def stem_from_url(url: str) -> str:
    """Last path segment, lowercased, without extension.

    Same rule as ``lambdas/file-name-decipher/utils.get_stub`` (that module is not part
    of this package, so the one-liner is re-implemented; a test asserts they agree).
    """
    file_name = urlparse(url).path.split("/")[-1]
    if not file_name:
        raise ValueError(f"URL has no file stem: {url}")
    lowered = file_name.lower()
    return lowered.rsplit(".", 1)[0] if "." in lowered else lowered


def _safe_stem(url: str) -> str:
    try:
        return stem_from_url(url)
    except ValueError:
        logger.warning("Variation URL has no file stem: %s", url)
        return ""


def _json_default(obj: Any) -> Any:
    """json.dumps fallback: DynamoDB Decimals become int/float, sets lists, bytes base64."""
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, set | frozenset):
        return sorted(obj)
    if isinstance(obj, bytes | bytearray):
        return base64.b64encode(bytes(obj)).decode("ascii")
    return str(obj)


def _content_md5(body: bytes) -> str:
    """Base64 MD5 for S3 ContentMD5 (transfer integrity only, not cryptographic)."""
    return base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode("ascii")


def _put_bytes(s3: Any, bucket: str, key: str, body: bytes, content_type: str) -> None:
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
        ContentMD5=_content_md5(body),
    )


def _put_to_snapshot_and_latest(
    s3: Any, bucket: str, date_prefix: str, rel_key: str, body: bytes, content_type: str
) -> None:
    for prefix in (date_prefix, LATEST_PREFIX):
        _put_bytes(s3, bucket, f"{prefix}/{rel_key}", body, content_type)


def _verify_s3_object_exists(s3: Any, bucket: str, key: str) -> None:
    """Verify object exists in S3 and has non-zero size; raise if not."""
    head = s3.head_object(Bucket=bucket, Key=key)
    size = head.get("ContentLength", 0)
    if size is None or size <= 0:
        raise RuntimeError(f"S3 object s3://{bucket}/{key} has invalid size: {size}")


def _is_not_found(exc: ClientError) -> bool:
    return str(exc.response.get("Error", {}).get("Code")) in S3_NOT_FOUND_CODES


# ---------------------------------------------------------------------------
# Step 1 — export tables
# ---------------------------------------------------------------------------


def scan_table(ddb: Any, table_name: str) -> list[dict[str, Any]]:
    """Full paginated scan, returned as plain Python items (Decimals for numbers)."""
    deserializer = TypeDeserializer()
    items: list[dict[str, Any]] = []
    for page in ddb.get_paginator("scan").paginate(TableName=table_name):
        for raw in page.get("Items", []):
            items.append({name: deserializer.deserialize(value) for name, value in raw.items()})
    return items


def export_tables(
    ddb: Any, s3: Any, settings: ArchiveSettings, snapshot: Snapshot
) -> dict[str, list[dict[str, Any]]]:
    """Scan each curriculum table and write it gzipped to the snapshot and latest prefixes."""
    tables: dict[str, list[dict[str, Any]]] = {}
    for art, table_name in settings.tables.items():
        items = scan_table(ddb, table_name)
        tables[art] = items
        body = gzip.compress(json.dumps(items, indent=2, default=_json_default).encode("utf-8"))
        _put_to_snapshot_and_latest(
            s3,
            settings.archive_bucket,
            snapshot.date_prefix,
            f"{TABLES_SUBDIR}/{art}.json.gz",
            body,
            "application/gzip",
        )
        logger.info("Exported %s: %d scroll records from %s", art, len(items), table_name)
    return tables


# ---------------------------------------------------------------------------
# Step 2 — manifest
# ---------------------------------------------------------------------------


def list_source_objects(s3: Any, bucket: str) -> tuple[dict[str, SourceObject], list[str]]:
    """Index the source bucket by stem; return (index, junk keys that failed the pattern)."""
    index: dict[str, SourceObject] = {}
    junk: list[str] = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            key = str(obj["Key"])
            if not VIDEO_KEY_PATTERN.match(key):
                junk.append(key)
                continue
            last_modified = obj.get("LastModified")
            stem = stem_from_url(key)
            index[stem] = SourceObject(
                key=key,
                stem=stem,
                size=int(obj.get("Size", 0)),
                etag=str(obj.get("ETag", "")).strip('"'),
                last_modified=(
                    last_modified.isoformat()
                    if isinstance(last_modified, datetime)
                    else str(last_modified or "")
                ),
            )
    logger.info("Source bucket %s: %d masters, %d junk keys", bucket, len(index), len(junk))
    return index, junk


def _technique_name(technique: dict[str, Any]) -> str:
    for key in TECHNIQUE_NAME_KEYS:
        value = technique.get(key)
        if value:
            return str(value)
    return ""


def _iter_variations(art: str, records: list[dict[str, Any]]) -> Iterator[Variation]:
    """Walk scroll records -> techniques -> variations in stable order."""
    for record in sorted(records, key=lambda r: str(r.get(DDB_SCROLL_NAME_KEY, ""))):
        scroll = str(record.get(DDB_SCROLL_NAME_KEY, ""))
        techniques = (record.get(DDB_MAP_KEY) or {}).get(DDB_ITEMS_KEY) or []
        for technique_index, technique in enumerate(techniques):
            for variation_index, url in enumerate(technique.get(DDB_VARIATIONS_KEY) or []):
                yield Variation(
                    art=art,
                    scroll=scroll,
                    technique_index=technique_index,
                    technique_number=str(technique.get(DDB_TECHNIQUE_NUMBER_KEY, "")),
                    technique_name=_technique_name(technique),
                    variation_index=variation_index,
                    hls_url=str(url),
                )


def _source_columns(source: SourceObject | None) -> dict[str, Any]:
    if source is None:
        return {
            "source_key": "",
            "source_size_bytes": None,
            "source_etag": "",
            "source_last_modified": "",
        }
    return {
        "source_key": source.key,
        "source_size_bytes": source.size,
        "source_etag": source.etag,
        "source_last_modified": source.last_modified,
    }


def _variation_row(variation: Variation, stem: str, source: SourceObject | None) -> dict[str, Any]:
    return {
        "art": variation.art,
        "scroll": variation.scroll,
        "technique_index": variation.technique_index,
        "technique_number": variation.technique_number,
        "technique_name": variation.technique_name,
        "variation_index": variation.variation_index,
        "stem": stem,
        "hls_url": variation.hls_url,
        **_source_columns(source),
        "status": STATUS_MAPPED if source is not None else STATUS_MISSING_SOURCE,
    }


def _orphan_row(source: SourceObject) -> dict[str, Any]:
    return {
        "art": ART_BY_STEM_PREFIX.get(source.stem[:1], ""),
        "scroll": "",
        "technique_index": None,
        "technique_number": "",
        "technique_name": "",
        "variation_index": None,
        "stem": source.stem,
        "hls_url": "",
        **_source_columns(source),
        "status": STATUS_ORPHAN_SOURCE,
    }


def _manifest_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    technique_index = row["technique_index"]
    variation_index = row["variation_index"]
    return (
        row["art"],
        row["status"] == STATUS_ORPHAN_SOURCE,
        row["scroll"],
        technique_index if technique_index is not None else -1,
        variation_index if variation_index is not None else -1,
        row["stem"],
    )


def build_manifest(
    tables: dict[str, list[dict[str, Any]]], source_index: dict[str, SourceObject]
) -> list[dict[str, Any]]:
    """One row per (technique, variation) plus one row per unreferenced source master."""
    rows: list[dict[str, Any]] = []
    referenced: set[str] = set()
    for art, records in tables.items():
        for variation in _iter_variations(art, records):
            stem = _safe_stem(variation.hls_url)
            referenced.add(stem)
            rows.append(_variation_row(variation, stem, source_index.get(stem)))
    for stem, source in source_index.items():
        if stem not in referenced:
            rows.append(_orphan_row(source))
    rows.sort(key=_manifest_sort_key)
    return rows


def _rows_to_csv(rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=MANIFEST_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {col: ("" if row.get(col) is None else row.get(col)) for col in MANIFEST_COLUMNS}
        )
    return buffer.getvalue().encode("utf-8")


def write_manifest(
    s3: Any, settings: ArchiveSettings, snapshot: Snapshot, rows: list[dict[str, Any]]
) -> None:
    """Write manifest.csv and manifest.json to the snapshot and latest prefixes."""
    manifest = {
        "generated_at": snapshot.timestamp,
        "source_bucket": settings.source_bucket,
        "archive_bucket": settings.archive_bucket,
        "git_sha": settings.git_sha,
        "columns": MANIFEST_COLUMNS,
        "row_count": len(rows),
        "rows": rows,
    }
    _put_to_snapshot_and_latest(
        s3,
        settings.archive_bucket,
        snapshot.date_prefix,
        "manifest.csv",
        _rows_to_csv(rows),
        "text/csv",
    )
    _put_to_snapshot_and_latest(
        s3,
        settings.archive_bucket,
        snapshot.date_prefix,
        "manifest.json",
        json.dumps(manifest, indent=2).encode("utf-8"),
        "application/json",
    )
    logger.info("Manifest written: %d rows", len(rows))


# ---------------------------------------------------------------------------
# Step 3 — docs + VERSION
# ---------------------------------------------------------------------------


def build_version_text(settings: ArchiveSettings, snapshot: Snapshot) -> str:
    """Contents of the VERSION file: which code produced this snapshot."""
    lines = [
        f"git_sha={settings.git_sha or 'unknown'}",
        f"lambda_function_name={os.environ.get('AWS_LAMBDA_FUNCTION_NAME', 'unknown')}",
        f"lambda_function_version={os.environ.get('AWS_LAMBDA_FUNCTION_VERSION', 'unknown')}",
        f"generated_at={snapshot.timestamp}",
    ]
    return "\n".join(lines) + "\n"


def copy_docs(
    s3: Any,
    settings: ArchiveSettings,
    snapshot: Snapshot,
    code_dir: Path | None = None,
) -> list[str]:
    """Upload the bundled documentation and VERSION file; returns the doc names uploaded."""
    base = code_dir or CODE_DIR
    uploaded: list[str] = []
    for rel_path in DOC_FILES:
        path = base / rel_path
        if not path.is_file():
            raise FileNotFoundError(f"Bundled doc missing from deployment package: {rel_path}")
        content_type = DOC_CONTENT_TYPES.get(path.suffix, DEFAULT_TEXT_CONTENT_TYPE)
        _put_to_snapshot_and_latest(
            s3,
            settings.archive_bucket,
            snapshot.date_prefix,
            f"{DOCS_SUBDIR}/{path.name}",
            path.read_bytes(),
            content_type,
        )
        uploaded.append(path.name)
    _put_to_snapshot_and_latest(
        s3,
        settings.archive_bucket,
        snapshot.date_prefix,
        VERSION_FILE,
        build_version_text(settings, snapshot).encode("utf-8"),
        DEFAULT_TEXT_CONTENT_TYPE,
    )
    logger.info("Docs uploaded: %s", ", ".join(uploaded))
    return uploaded


# ---------------------------------------------------------------------------
# Step 4 — sync videos
# ---------------------------------------------------------------------------


def _archived_etags(s3: Any, bucket: str, key: str) -> tuple[str | None, str | None]:
    """(stored source-etag metadata, the archived object's own ETag); (None, None) if absent."""
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if _is_not_found(exc):
            return None, None
        raise
    metadata = head.get("Metadata") or {}
    return metadata.get(META_SOURCE_ETAG), str(head.get("ETag", "")).strip('"')


def _needs_copy(s3: Any, bucket: str, source: SourceObject) -> bool:
    """Copy when absent, or when neither the stored source ETag nor the object's own ETag matches.

    The own-ETag check lets objects seeded by `aws s3 sync` (which stores no metadata but
    reproduces the source ETag when part sizes match) count as in sync without a re-copy.
    """
    stored_etag, own_etag = _archived_etags(s3, bucket, f"{VIDEO_PREFIX}/{source.key}")
    if stored_etag is None and own_etag is None:
        return True
    return source.etag not in (stored_etag, own_etag)


def _copy_video(s3: Any, settings: ArchiveSettings, source: SourceObject) -> None:
    s3.copy_object(
        Bucket=settings.archive_bucket,
        Key=f"{VIDEO_PREFIX}/{source.key}",
        CopySource={"Bucket": settings.source_bucket, "Key": source.key},
        MetadataDirective="REPLACE",
        ContentType=VIDEO_CONTENT_TYPE,
        Metadata={
            META_SOURCE_ETAG: source.etag,
            META_SOURCE_LAST_MODIFIED: source.last_modified,
            META_STEM: source.stem,
        },
    )


def sync_videos(
    s3: Any,
    settings: ArchiveSettings,
    source_index: dict[str, SourceObject],
    remaining_ms: Callable[[], int],
) -> SyncResult:
    """Server-side copy every new or changed master into videos/; never deletes.

    Stops starting copies once less than TIME_RESERVE_MS remains and records the keys it
    did not reach so the next monthly run (or a manual invoke) picks them up.
    """
    result = SyncResult()
    pending = sorted(source_index.values(), key=lambda s: s.key)
    for position, source in enumerate(pending):
        if remaining_ms() < TIME_RESERVE_MS:
            result.skipped_for_time = [s.key for s in pending[position:]]
            logger.warning(
                "Time budget reached; %d masters left for the next run",
                len(result.skipped_for_time),
            )
            break
        if not _needs_copy(s3, settings.archive_bucket, source):
            result.unchanged += 1
            continue
        _copy_video(s3, settings, source)
        result.copied.append(source.key)
        logger.info("Copied %s (%d bytes)", source.key, source.size)
    logger.info(
        "Video sync: %d copied, %d unchanged, %d skipped for time",
        len(result.copied),
        result.unchanged,
        len(result.skipped_for_time),
    )
    return result


# ---------------------------------------------------------------------------
# Step 5 — verify + notify
# ---------------------------------------------------------------------------


def verify_snapshot(s3: Any, bucket: str, snapshot: Snapshot, arts: list[str]) -> None:
    """head_object on the manifest (both prefixes) and every table snapshot."""
    keys = [f"{prefix}/manifest.csv" for prefix in (snapshot.date_prefix, LATEST_PREFIX)]
    keys.append(f"{snapshot.date_prefix}/manifest.json")
    keys.extend(f"{snapshot.date_prefix}/{TABLES_SUBDIR}/{art}.json.gz" for art in arts)
    for key in keys:
        _verify_s3_object_exists(s3, bucket, key)
    logger.info("Verified %d snapshot objects", len(keys))


def archive_stats(s3: Any, bucket: str) -> tuple[int, int]:
    """(object count, total bytes) of current versions in the archive bucket."""
    count = 0
    total = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            count += 1
            total += int(obj.get("Size", 0))
    return count, total


def _summarize(
    rows: list[dict[str, Any]],
    sync: SyncResult,
    junk: list[str],
    stats: tuple[int, int],
    snapshot: Snapshot,
    elapsed_s: float,
) -> dict[str, Any]:
    rows_by_art = Counter(str(row["art"]) for row in rows)
    status_counts = Counter(str(row["status"]) for row in rows)
    return {
        "status": "success",
        "snapshot_prefix": snapshot.date_prefix,
        "rows_by_art": dict(sorted(rows_by_art.items())),
        STATUS_MAPPED: status_counts[STATUS_MAPPED],
        STATUS_MISSING_SOURCE: status_counts[STATUS_MISSING_SOURCE],
        STATUS_ORPHAN_SOURCE: status_counts[STATUS_ORPHAN_SOURCE],
        "missing_source_stems": [
            row["stem"] for row in rows if row["status"] == STATUS_MISSING_SOURCE
        ][:MAX_LISTED_KEYS],
        "orphan_source_keys": [
            row["source_key"] for row in rows if row["status"] == STATUS_ORPHAN_SOURCE
        ][:MAX_LISTED_KEYS],
        "videos_copied": len(sync.copied),
        "videos_unchanged": sync.unchanged,
        "videos_skipped_for_time": len(sync.skipped_for_time),
        "copied_keys": sync.copied[:MAX_LISTED_KEYS],
        "skipped_keys": sync.skipped_for_time[:MAX_LISTED_KEYS],
        "junk_keys": junk,
        "archive_object_count": stats[0],
        "archive_total_bytes": stats[1],
        "elapsed_seconds": round(elapsed_s, 1),
    }


def format_summary_message(summary: dict[str, Any]) -> str:
    """Human-readable SNS body for a successful run."""
    by_art = ", ".join(f"{art}={count}" for art, count in summary["rows_by_art"].items())
    gib = summary["archive_total_bytes"] / (1024**3)
    lines = [
        f"Snapshot: {summary['snapshot_prefix']}",
        f"Manifest rows by art: {by_art or 'none'}",
        f"mapped={summary[STATUS_MAPPED]} "
        f"missing_source={summary[STATUS_MISSING_SOURCE]} "
        f"orphan_source={summary[STATUS_ORPHAN_SOURCE]}",
        f"Videos: copied={summary['videos_copied']} unchanged={summary['videos_unchanged']} "
        f"skipped_for_time={summary['videos_skipped_for_time']}",
        f"Junk keys (not archived): {summary['junk_keys'] or 'none'}",
        f"Archive: {summary['archive_object_count']} objects, {gib:.2f} GiB",
        f"Elapsed: {summary['elapsed_seconds']} s",
    ]
    for label in ("missing_source_stems", "orphan_source_keys", "copied_keys", "skipped_keys"):
        if summary[label]:
            lines.append(f"{label}: {', '.join(summary[label])}")
    return "\n".join(lines) + "\n"


def _publish(sns: Any, topic_arn: str | None, subject: str, message: str) -> None:
    if not topic_arn:
        return
    sns.publish(TopicArn=topic_arn, Subject=subject, Message=message)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def _new_snapshot(now: datetime) -> Snapshot:
    return Snapshot(
        date_prefix=f"{SNAPSHOT_PREFIX}/{now.strftime('%Y-%m-%d')}",
        timestamp=now.isoformat(),
    )


def run_archive(
    settings: ArchiveSettings, clients: dict[str, Any], remaining_ms: Callable[[], int]
) -> dict[str, Any]:
    """Execute the five steps and return the run summary."""
    started = time.monotonic()
    snapshot = _new_snapshot(datetime.now(UTC))
    s3 = clients["s3_archive"]
    tables = export_tables(clients["dynamodb"], s3, settings, snapshot)
    source_index, junk = list_source_objects(clients["s3_source"], settings.source_bucket)
    rows = build_manifest(tables, source_index)
    write_manifest(s3, settings, snapshot, rows)
    copy_docs(s3, settings, snapshot)
    sync = sync_videos(s3, settings, source_index, remaining_ms)
    verify_snapshot(s3, settings.archive_bucket, snapshot, list(tables))
    stats = archive_stats(s3, settings.archive_bucket)
    return _summarize(rows, sync, junk, stats, snapshot, time.monotonic() - started)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """EventBridge entry point (also invoked by hand). Failure -> SNS + re-raise."""
    settings = load_settings()
    clients = _get_clients(settings.archive_region)
    logger.info(
        "Curriculum archive started: %s -> %s", settings.source_bucket, settings.archive_bucket
    )
    try:
        summary = run_archive(settings, clients, _remaining_ms_fn(context))
    except Exception as exc:
        message = f"Curriculum archive failed: {type(exc).__name__}: {exc}"
        logger.error(message)
        _publish(clients["sns"], settings.sns_topic_arn, SUBJECT_FAILURE, message)
        raise
    _publish(
        clients["sns"],
        settings.sns_topic_arn,
        f"{SUBJECT_SUCCESS} - {summary['snapshot_prefix']}",
        format_summary_message(summary),
    )
    logger.info("Curriculum archive completed: %s", json.dumps(summary, default=str))
    return summary
