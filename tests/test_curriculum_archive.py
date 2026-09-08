"""
Tests for the curriculum-archive Lambda: table export, manifest join, docs bundle,
cross-region video sync with ETag / time-budget rules, verification, and SNS paths.
"""

import csv
import gzip
import importlib.util
import io
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

import utils as decipher_utils  # lambdas/file-name-decipher (on sys.path via conftest)

REPO_ROOT = Path(__file__).resolve().parent.parent
LAMBDA_DIR = REPO_ROOT / "lambdas" / "curriculum-archive"
APP_PATH = LAMBDA_DIR / "app.py"

CDN = "https://d1gdfk972ekc67.cloudfront.net"
ETAG_SHARED = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-3"
LAST_MODIFIED = datetime(2025, 5, 4, 3, 2, 1, tzinfo=UTC)

TEST_ENV = {
    "AWS_REGION": "us-west-1",
    "AWS_DDB_AIKIDO_TABLE_NAME": "Aikido-test",
    "AWS_DDB_BATTODO_TABLE_NAME": "Battodo-test",
    "AWS_DDB_DANZAN_RYU_TABLE_NAME": "DanzanRyu-test",
    "SOURCE_VIDEO_BUCKET": "source-bucket",
    "ARCHIVE_BUCKET": "archive-bucket",
    "ARCHIVE_BUCKET_REGION": "us-west-2",
    "SNS_SUPPORT_TOPIC_ARN": "arn:aws:sns:us-west-1:123:support",
    "DEPLOYED_GIT_SHA": "abc1234",
}


def _load_app():
    spec = importlib.util.spec_from_file_location("curriculum_archive_app", APP_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def app():
    with patch.dict("os.environ", TEST_ENV):
        yield _load_app()


class _Context:
    """Minimal Lambda context with a scripted remaining-time budget."""

    def __init__(self, remaining_ms=900_000):
        self._remaining = remaining_ms

    def get_remaining_time_in_millis(self):
        return self._remaining


# ---------------------------------------------------------------------------
# Fixtures: two-scroll curriculum tables + a source bucket
# ---------------------------------------------------------------------------


def _hls(stem):
    return f"{CDN}/f89c1538-9369-481b-9e26-0b6f7cac466a/hls/{stem}.m3u8"


def _ddb_str(value):
    return {"S": value}


def _ddb_scroll(name, techniques):
    """DynamoDB-JSON scroll record as the client's scan paginator returns it."""
    return {
        "id": _ddb_str(f"id-{name}"),
        "Name": _ddb_str(name),
        "_version": {"N": "3"},
        "map": {
            "M": {
                "Name": _ddb_str(name),
                "Items": {"L": [{"M": t} for t in techniques]},
            }
        },
    }


def _technique(number, name_key, name, stems):
    return {
        "Number": _ddb_str(str(number)),
        name_key: _ddb_str(name),
        "Variations": {"L": [_ddb_str(_hls(s)) for s in stems]},
    }


AIKIDO_ITEMS = [
    _ddb_scroll(
        "katate_mochi",
        [
            _technique(1, "Name", "soft", ["a2101a", "a2101b"]),
            _technique(2, "Name", "hard", []),  # technique without video: not in manifest
        ],
    ),
    _ddb_scroll("bo_drills", [_technique(1, "Name", "drill one", ["a0101a"])]),
]
BATTODO_ITEMS = [
    _ddb_scroll(
        "tameshigiri",
        [_technique(1, "Techniques", "Gaiden", ["b0101a"])],  # b0101a has no master
    ),
]
DANZAN_RYU_ITEMS = [
    _ddb_scroll(
        "shime_groundflow",
        [_technique(1, "Name", "Groundflow1", ["dj1a", "dn1a"])],  # shared clip
    ),
]

SOURCE_OBJECTS = {
    "a2101a.mov": ("11111111111111111111111111111111", 100),
    "a2101b.mov": ("22222222222222222222222222222222-2", 200),
    "a0101a.mov": ("33333333333333333333333333333333", 300),
    "dj1a.mov": (ETAG_SHARED, 513),
    "dn1a.mov": (ETAG_SHARED, 513),
    "a9999z.mov": ("99999999999999999999999999999999", 900),  # orphan
    "bi01ca.movv": ("deadbeefdeadbeefdeadbeefdeadbeef", 5),  # junk
}


class FakeDynamoDB:
    def __init__(self, tables, fail=False):
        self._tables = tables
        self._fail = fail

    def get_paginator(self, name):
        assert name == "scan"
        return self

    def paginate(self, TableName):
        if self._fail:
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "Scan")
        items = self._tables[TableName]
        # Two pages to prove pagination is honoured.
        yield {"Items": items[:1]}
        yield {"Items": items[1:]}


class FakeSourceS3:
    """Source bucket: listing with storage class, head (Restore header), restore requests."""

    def __init__(self, objects, storage_class=None, restore_headers=None):
        self._objects = objects
        self._storage_class = storage_class or {}  # key -> class (default STANDARD)
        self._restore = restore_headers or {}  # key -> Restore header value
        self.restore_calls = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, Bucket):
        contents = [
            {
                "Key": key,
                "Size": size,
                "ETag": f'"{etag}"',
                "LastModified": LAST_MODIFIED,
                "StorageClass": self._storage_class.get(key, "STANDARD"),
            }
            for key, (etag, size) in sorted(self._objects.items())
        ]
        yield {"Contents": contents[:3]}
        yield {"Contents": contents[3:]}

    def head_object(self, Bucket, Key):
        head = {"ContentLength": 1, "StorageClass": self._storage_class.get(Key, "STANDARD")}
        if Key in self._restore:
            head["Restore"] = self._restore[Key]
        return head

    def restore_object(self, **kwargs):
        self.restore_calls.append(kwargs)
        if self._restore.get(kwargs["Key"], "").startswith('ongoing-request="true"'):
            raise ClientError({"Error": {"Code": "RestoreAlreadyInProgress"}}, "RestoreObject")


def _not_found():
    return ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")


class FakeArchiveS3:
    """In-memory archive bucket: put/copy/head/list with per-object ETag + metadata."""

    def __init__(self, existing=None):
        # key -> {"Body": bytes, "ETag": str, "Metadata": dict}
        self.objects = dict(existing or {})
        self.copy_calls = []
        self.put_calls = []

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        body = kwargs["Body"]
        self.objects[kwargs["Key"]] = {"Body": body, "ETag": "put-etag", "Metadata": {}}

    def copy_object(self, **kwargs):
        self.copy_calls.append(kwargs)
        self.objects[kwargs["Key"]] = {
            "Body": b"",
            "ETag": "copied-" + kwargs["Metadata"]["source-etag"],
            "Metadata": dict(kwargs["Metadata"]),
        }

    def head_object(self, Bucket, Key):
        obj = self.objects.get(Key)
        if obj is None:
            raise _not_found()
        return {
            "ContentLength": len(obj["Body"]) or 1,
            "ETag": f'"{obj["ETag"]}"',
            "Metadata": obj["Metadata"],
        }

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, Bucket):
        yield {
            "Contents": [
                {"Key": key, "Size": len(obj["Body"]) or 1} for key, obj in self.objects.items()
            ]
        }


def _tables():
    return {
        "Aikido-test": AIKIDO_ITEMS,
        "Battodo-test": BATTODO_ITEMS,
        "DanzanRyu-test": DANZAN_RYU_ITEMS,
    }


def _docs_dir(tmp_path, app):
    """A code dir holding every DOC_FILES entry so copy_docs succeeds in tests."""
    for rel in app.DOC_FILES:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rel}\n")
    return tmp_path


def _clients(archive, ddb_fail=False):
    return {
        "dynamodb": FakeDynamoDB(_tables(), fail=ddb_fail),
        "s3_source": FakeSourceS3(SOURCE_OBJECTS),
        "s3_archive": archive,
        "sns": MagicMock(),
    }


# ---------------------------------------------------------------------------
# Stem rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        _hls("a2101a"),
        "https://bucket.s3.amazonaws.com/path/A0101X.M3U8?version=1",
        "/local/path/c01a.m3u8",
        "dj1a.mov",
        "filename",
    ],
)
def test_stem_rule_matches_file_name_decipher(app, url):
    """The re-implemented stem rule must agree with file-name-decipher's get_stub."""
    assert app.stem_from_url(url) == decipher_utils.get_stub(url)


def test_stem_rule_rejects_empty(app):
    with pytest.raises(ValueError, match="no file stem"):
        app.stem_from_url("https://cdn.example.com/")


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _manifest_from_fixture(app):
    ddb = FakeDynamoDB(_tables())
    settings = app.load_settings()
    tables = {art: app.scan_table(ddb, name) for art, name in settings.tables.items()}
    index, junk = app.list_source_objects(FakeSourceS3(SOURCE_OBJECTS), "source-bucket")
    return app.build_manifest(tables, index), junk


def test_manifest_rows_and_statuses(app):
    rows, junk = _manifest_from_fixture(app)
    by_stem = {(r["stem"], r["status"]): r for r in rows}

    # 6 variations + 1 orphan; the technique without variations contributes nothing.
    assert len(rows) == 7
    assert junk == ["bi01ca.movv"]
    assert all(r["source_key"] != "bi01ca.movv" for r in rows)

    mapped = by_stem[("a2101a", "mapped")]
    assert mapped["art"] == "aikido"
    assert mapped["scroll"] == "katate_mochi"
    assert mapped["technique_index"] == 0
    assert mapped["technique_number"] == "1"
    assert mapped["technique_name"] == "soft"
    assert mapped["variation_index"] == 0
    assert mapped["hls_url"] == _hls("a2101a")
    assert mapped["source_key"] == "a2101a.mov"
    assert mapped["source_size_bytes"] == 100
    assert mapped["source_etag"] == "11111111111111111111111111111111"
    assert mapped["source_last_modified"] == LAST_MODIFIED.isoformat()
    assert by_stem[("a2101b", "mapped")]["variation_index"] == 1

    # Battodo display name comes from `Techniques`; its master is absent.
    missing = by_stem[("b0101a", "missing_source")]
    assert missing["technique_name"] == "Gaiden"
    assert missing["source_key"] == ""
    assert missing["source_size_bytes"] is None

    # Shared clip: two stems, same bytes -> two mapped rows with identical etag/size.
    dj, dn = by_stem[("dj1a", "mapped")], by_stem[("dn1a", "mapped")]
    assert dj["source_etag"] == dn["source_etag"] == ETAG_SHARED
    assert dj["source_size_bytes"] == dn["source_size_bytes"] == 513
    assert (dj["variation_index"], dn["variation_index"]) == (0, 1)

    orphan = by_stem[("a9999z", "orphan_source")]
    assert orphan["art"] == "aikido"
    assert orphan["scroll"] == "" and orphan["technique_index"] is None
    assert orphan["source_key"] == "a9999z.mov"


def test_manifest_sort_order(app):
    rows, _ = _manifest_from_fixture(app)
    keys = [
        (r["art"], r["scroll"], r["technique_index"], r["variation_index"], r["status"])
        for r in rows
    ]
    assert keys == [
        ("aikido", "bo_drills", 0, 0, "mapped"),
        ("aikido", "katate_mochi", 0, 0, "mapped"),
        ("aikido", "katate_mochi", 0, 1, "mapped"),
        ("aikido", "", None, None, "orphan_source"),
        ("battodo", "tameshigiri", 0, 0, "missing_source"),
        ("danzan_ryu", "shime_groundflow", 0, 0, "mapped"),
        ("danzan_ryu", "shime_groundflow", 0, 1, "mapped"),
    ]


def test_manifest_csv_columns_and_blanks(app):
    rows, _ = _manifest_from_fixture(app)
    text = app._rows_to_csv(rows).decode("utf-8")
    parsed = list(csv.DictReader(io.StringIO(text)))
    assert list(parsed[0].keys()) == app.MANIFEST_COLUMNS
    orphan = next(r for r in parsed if r["status"] == "orphan_source")
    assert orphan["technique_index"] == "" and orphan["source_size_bytes"] == "900"


def test_json_default_converts_decimals(app):
    assert json.loads(
        json.dumps({"a": Decimal("3"), "b": Decimal("1.5")}, default=app._json_default)
    ) == {
        "a": 3,
        "b": 1.5,
    }


# ---------------------------------------------------------------------------
# Video sync
# ---------------------------------------------------------------------------


def _source(app, key, etag, size=1, storage_class="STANDARD"):
    return app.SourceObject(
        key=key,
        stem=key[:-4],
        size=size,
        etag=etag,
        last_modified="lm",
        storage_class=storage_class,
    )


def _sync(app, archive, index, remaining=lambda: 900_000, source_s3=None):
    return app.sync_videos(
        source_s3 or FakeSourceS3({}), archive, app.load_settings(), index, remaining
    )


def test_sync_skips_on_metadata_etag_match_and_copies_on_mismatch(app):
    archive = FakeArchiveS3(
        {
            "videos/same.mov": {"Body": b"x", "ETag": "other", "Metadata": {"source-etag": "e1"}},
            "videos/changed.mov": {
                "Body": b"x",
                "ETag": "other",
                "Metadata": {"source-etag": "old"},
            },
        }
    )
    index = {
        "same": _source(app, "same.mov", "e1"),
        "changed": _source(app, "changed.mov", "e2"),
        "new": _source(app, "new.mov", "e3"),
    }
    result = _sync(app, archive, index)
    assert result.copied == ["changed.mov", "new.mov"]
    assert result.unchanged == 1
    assert result.skipped_for_time == []
    call = next(c for c in archive.copy_calls if c["Key"] == "videos/changed.mov")
    assert call["CopySource"] == {"Bucket": "source-bucket", "Key": "changed.mov"}
    assert call["Bucket"] == "archive-bucket"
    assert call["MetadataDirective"] == "REPLACE"
    assert call["Metadata"] == {
        "source-etag": "e2",
        "source-last-modified": "lm",
        "stem": "changed",
    }


def test_sync_accepts_own_etag_match_for_seeded_objects(app):
    """Objects seeded by `aws s3 sync` carry no metadata but reproduce the source ETag."""
    archive = FakeArchiveS3({"videos/seeded.mov": {"Body": b"x", "ETag": "e1", "Metadata": {}}})
    result = _sync(app, archive, {"seeded": _source(app, "seeded.mov", "e1")})
    assert result.unchanged == 1 and result.copied == [] and archive.copy_calls == []


def test_sync_stops_at_time_budget_and_records_skipped(app):
    archive = FakeArchiveS3()
    index = {k[:-4]: _source(app, k, "e") for k in ("a.mov", "b.mov", "c.mov", "d.mov")}
    budget = iter([500_000, 500_000, 30_000])  # third check is under the 60 s reserve
    result = _sync(app, archive, index, remaining=lambda: next(budget))
    assert result.copied == ["a.mov", "b.mov"]
    assert result.skipped_for_time == ["c.mov", "d.mov"]
    assert "videos/c.mov" not in archive.objects


def test_sync_reraises_non_404_head_errors(app):
    archive = MagicMock()
    archive.head_object.side_effect = ClientError({"Error": {"Code": "403"}}, "HeadObject")
    with pytest.raises(ClientError):
        _sync(app, archive, {"a": _source(app, "a.mov", "e")})


def test_sync_requests_restore_for_deep_archive_sources_and_copies_when_restored(app):
    """Archived masters: no Restore header -> Bulk restore requested; in progress -> awaited;
    completed -> copied. STANDARD masters copy directly."""
    index = {
        "cold": _source(app, "cold.mov", "e1", storage_class="DEEP_ARCHIVE"),
        "thawing": _source(app, "thawing.mov", "e2", storage_class="GLACIER"),
        "thawed": _source(app, "thawed.mov", "e3", storage_class="DEEP_ARCHIVE"),
        "warm": _source(app, "warm.mov", "e4"),
    }
    source_s3 = FakeSourceS3(
        {},
        restore_headers={
            "thawing.mov": 'ongoing-request="true"',
            "thawed.mov": 'ongoing-request="false", expiry-date="Fri, 17 Oct 2026 00:00:00 GMT"',
        },
    )
    archive = FakeArchiveS3()
    result = _sync(app, archive, index, source_s3=source_s3)
    assert result.restore_requested == ["cold.mov"]
    assert result.awaiting_restore == ["thawing.mov"]
    assert result.copied == ["thawed.mov", "warm.mov"]
    assert [c["Key"] for c in source_s3.restore_calls] == ["cold.mov"]
    assert source_s3.restore_calls[0]["RestoreRequest"] == {
        "Days": app.SOURCE_RESTORE_DAYS,
        "GlacierJobParameters": {"Tier": "Bulk"},
    }
    assert "videos/cold.mov" not in archive.objects


def test_sync_treats_restore_already_in_progress_as_awaiting(app):
    """A racing restore request (409 RestoreAlreadyInProgress) is not an error."""

    class RacingSourceS3(FakeSourceS3):
        def head_object(self, Bucket, Key):  # restore header not visible yet
            return {"ContentLength": 1}

    source_s3 = RacingSourceS3({}, restore_headers={"x.mov": 'ongoing-request="true"'})
    result = _sync(
        app,
        FakeArchiveS3(),
        {"x": _source(app, "x.mov", "e", storage_class="DEEP_ARCHIVE")},
        source_s3=source_s3,
    )
    assert result.awaiting_restore == ["x.mov"] and result.restore_requested == []


# ---------------------------------------------------------------------------
# Handler end to end
# ---------------------------------------------------------------------------


def test_handler_writes_snapshot_and_publishes_summary(app, tmp_path):
    docs_dir = _docs_dir(tmp_path, app)
    archive = FakeArchiveS3()
    clients = _clients(archive)
    with (
        patch.object(app, "_get_clients", return_value=clients),
        patch.object(app, "CODE_DIR", docs_dir),
    ):
        summary = app.lambda_handler({}, _Context())

    date_prefix = summary["snapshot_prefix"]
    assert date_prefix.startswith("snapshots/20")
    assert summary["status"] == "success"
    assert summary["rows_by_art"] == {"aikido": 4, "battodo": 1, "danzan_ryu": 2}
    assert (summary["mapped"], summary["missing_source"], summary["orphan_source"]) == (5, 1, 1)
    assert summary["missing_source_stems"] == ["b0101a"]
    assert summary["orphan_source_keys"] == ["a9999z.mov"]
    assert summary["junk_keys"] == ["bi01ca.movv"]
    assert summary["videos_copied"] == 6 and summary["videos_skipped_for_time"] == 0
    assert summary["videos_restore_requested"] == 0 and summary["videos_awaiting_restore"] == 0
    assert "bi01ca.movv" not in {c["CopySource"]["Key"] for c in archive.copy_calls}

    keys = set(archive.objects)
    for prefix in (date_prefix, "snapshots/latest"):
        assert {f"{prefix}/manifest.csv", f"{prefix}/manifest.json", f"{prefix}/VERSION"} <= keys
        assert {
            f"{prefix}/tables/{art}.json.gz" for art in ("aikido", "battodo", "danzan_ryu")
        } <= keys
        assert {f"{prefix}/docs/{Path(rel).name}" for rel in app.DOC_FILES} <= keys
    assert {f"videos/{k}" for k in SOURCE_OBJECTS if k.endswith(".mov")} <= keys

    table = json.loads(
        gzip.decompress(archive.objects[f"{date_prefix}/tables/aikido.json.gz"]["Body"])
    )
    assert [rec["Name"] for rec in table] == ["katate_mochi", "bo_drills"]
    assert table[0]["_version"] == 3  # Decimal -> int
    assert table[0]["map"]["Items"][0]["Variations"] == [_hls("a2101a"), _hls("a2101b")]

    manifest = json.loads(archive.objects[f"{date_prefix}/manifest.json"]["Body"])
    assert manifest["columns"] == app.MANIFEST_COLUMNS
    assert manifest["row_count"] == 7 and manifest["git_sha"] == "abc1234"

    version = archive.objects[f"{date_prefix}/VERSION"]["Body"].decode()
    assert "git_sha=abc1234" in version

    for call in archive.put_calls:
        assert call["ContentMD5"]  # every upload carries an integrity header

    sns = clients["sns"]
    sns.publish.assert_called_once()
    publish = sns.publish.call_args.kwargs
    assert publish["TopicArn"] == TEST_ENV["SNS_SUPPORT_TOPIC_ARN"]
    assert publish["Subject"] == f"Curriculum Archive OK - {date_prefix}"
    assert "missing_source=1 orphan_source=1" in publish["Message"]
    assert "bi01ca.movv" in publish["Message"]


def test_handler_scan_failure_publishes_sns_and_reraises(app):
    clients = _clients(FakeArchiveS3(), ddb_fail=True)
    with patch.object(app, "_get_clients", return_value=clients), pytest.raises(ClientError):
        app.lambda_handler({}, _Context())
    publish = clients["sns"].publish.call_args.kwargs
    assert publish["Subject"] == "Curriculum Archive FAILURE"
    assert "ProvisionedThroughputExceededException" in publish["Message"]


def test_handler_without_sns_topic_does_not_publish(app):
    with patch.dict("os.environ", {"SNS_SUPPORT_TOPIC_ARN": ""}):
        clients = _clients(FakeArchiveS3(), ddb_fail=True)
        with patch.object(app, "_get_clients", return_value=clients), pytest.raises(ClientError):
            app.lambda_handler({}, _Context())
    clients["sns"].publish.assert_not_called()


def test_missing_required_env_raises(app):
    with (
        patch.dict("os.environ", {"ARCHIVE_BUCKET": ""}),
        pytest.raises(ValueError, match="ARCHIVE_BUCKET"),
    ):
        app.load_settings()


def test_copy_docs_fails_loudly_when_bundle_missing(app, tmp_path):
    settings = app.load_settings()
    snapshot = app.Snapshot(date_prefix="snapshots/2026-01-01", timestamp="t")
    with pytest.raises(FileNotFoundError, match="TECHNIQUE_TO_FILENAME_REFERENCE.md"):
        app.copy_docs(FakeArchiveS3(), settings, snapshot, code_dir=tmp_path)


def test_verify_snapshot_rejects_empty_object(app):
    archive = MagicMock()
    archive.head_object.return_value = {"ContentLength": 0}
    with pytest.raises(RuntimeError, match="invalid size"):
        app.verify_snapshot(archive, "b", app.Snapshot("snapshots/x", "t"), ["aikido"])


# ---------------------------------------------------------------------------
# Packaging: every file the Lambda uploads must actually be in the deploy zip
# ---------------------------------------------------------------------------


def test_doc_files_are_packaged(app):
    config = json.loads((LAMBDA_DIR / "config.json").read_text())
    bundled = {Path(p).name: REPO_ROOT / p for p in config.get("bundle_files", [])}
    for rel in app.DOC_FILES:
        parts = Path(rel).parts
        if parts[0] == "bundle":
            assert parts[-1] in bundled, f"{rel} not listed in config.json bundle_files"
            assert bundled[parts[-1]].is_file(), f"bundle_files source missing: {rel}"
        elif parts[0] == "common":
            assert (REPO_ROOT / rel).is_file(), f"{rel} missing from common/ (bundled by deploy)"
        else:
            assert (LAMBDA_DIR / rel).is_file(), f"{rel} missing from the lambda directory"


def test_config_shape(app):
    config = json.loads((LAMBDA_DIR / "config.json").read_text())
    assert config["function_name"] == "suigetsukan-curriculum-archive"
    assert config["timeout"] == 900
    assert config["event_sources"][0]["schedule_expression"] == "cron(0 4 1 * ? *)"
    for name in app.ART_TABLE_ENV.values():
        assert name in config["env_vars"]
    for name in ("SOURCE_VIDEO_BUCKET", "ARCHIVE_BUCKET", "ARCHIVE_BUCKET_REGION"):
        assert name in config["env_vars"]
