# Curriculum Archive

Catastrophic-failure backup of the curriculum: every source video master, the three
curriculum DynamoDB tables, a flat manifest tying each video to its art / scroll /
technique / variation, and the documentation needed to read all of it with **no AWS
account**. Refreshed monthly by the `suigetsukan-curriculum-archive` Lambda; pullable to
a local drive with one script.

Before this existed the only backup was AWS Backup on the DynamoDB tables (same account,
same region). The 543 video masters (22.8 GB) had no second copy, and nothing outside the
running system tied a video file to the technique it demonstrates.

## Architecture

```
us-west-1 (running system)                       us-west-2 (archive)
──────────────────────────                       ───────────────────
DynamoDB  Aikido / Battodo / DanzanRyu ─ scan ─┐
                                               ├─► s3://suigetsukan-curriculum-archive/
S3 source-video bucket (<stem>.mov masters) ───┤      snapshots/<date>/…   STANDARD
   │ list + cross-region server-side copy      │      snapshots/latest/…   STANDARD (pointer)
   └───────────────────────────────────────────┘      videos/<stem>.mov    DEEP_ARCHIVE after 1 day
                 ▲
   EventBridge cron(0 4 1 * ? *) ─► Lambda suigetsukan-curriculum-archive (900 s, 512 MB)
                                          └─► SNS SNS_SUPPORT_TOPIC_ARN (summary / failure)
```

- **Bucket** (`infra/archive-bucket.yaml`, deploy once in us-west-2): versioning, Object
  Lock in **Governance** mode with a 1-year default retention, all public access blocked,
  SSE-S3, bucket policy denying `s3:DeleteBucket` and non-TLS access, `DeletionPolicy:
  Retain`. Object Lock is what makes the archive survive a compromised key or a
  fat-fingered `rm`; it can only be enabled at bucket creation.
- **Lifecycle**: `videos/` (current and noncurrent versions) → `DEEP_ARCHIVE` after 1 day
  (~$0.02/month for 22.8 GB). `snapshots/` stays in `STANDARD` so the manifest and docs
  are instantly readable; noncurrent versions of the rewritten `snapshots/latest/`
  expire after 400 days (longer than the lock, so lifecycle never fights Object Lock).
- **HLS renditions are not archived.** They are derivable from the masters (MediaConvert
  or ffmpeg); the manifest records each variation's HLS URL so the destination key can be
  reconstructed if needed.
- **The archive is never pruned by code.** The Lambda only adds and overwrites; the seed
  script uses `aws s3 sync` without `--delete`.

## Schedule and what one run does

EventBridge rule `suigetsukan-curriculum-archive-Schedule`, `cron(0 4 1 * ? *)` (04:00 UTC
on the 1st of every month). Each run, in order:

1. **Export tables** — full paginated scan of each table →
   `snapshots/<YYYY-MM-DD>/tables/<art>.json.gz` (raw items, Decimal → int/float) and the
   same under `snapshots/latest/`.
2. **Build manifest** — walk table → scroll → technique → variation; derive the `stem` from
   each HLS URL (last path segment, lowercased, no extension: the file-name-decipher rule);
   list the source bucket once into `stem → (key, size, ETag, last modified)`; join. One row
   per (technique, variation) plus one row per source master no variation references →
   `manifest.csv` and `manifest.json` under both prefixes.
3. **Copy docs** — `TECHNIQUE_TO_FILENAME_REFERENCE.md`, the three `common/*_mappings.py`
   modules, and `RESTORE.md` → `snapshots/<date>/docs/` (and `latest/`), plus a `VERSION`
   file with the deployed git SHA (`DEPLOYED_GIT_SHA`, set by the pipeline from
   `github.sha`) and the Lambda version.
4. **Sync videos** — for every source master matching `^[abd][a-z0-9]+\.mov$`: if
   `videos/<key>` is absent, or neither its stored `x-amz-meta-source-etag` nor its own ETag
   equals the source ETag, `copy_object` cross-region (server-side, us-west-2 client with
   `CopySource` in us-west-1), stamping `source-etag`, `source-last-modified`, `stem`
   metadata. Keys failing the pattern (e.g. `bi01ca.movv`) are listed as junk, never
   copied. Copying stops with 60 s of Lambda time left; skipped keys are reported and the
   next run continues.
5. **Verify + notify** — `head_object` on the manifest and each table snapshot, then an SNS
   summary. Any exception → SNS failure message and re-raise, so the invocation shows as
   failed in CloudWatch.

## Bucket layout

```
snapshots/latest/                 <- pointer; rewritten every run
snapshots/YYYY-MM-DD/
  manifest.csv                    <- see columns below
  manifest.json                   <- same rows + generated_at, buckets, git_sha, columns
  tables/{aikido,battodo,danzan_ryu}.json.gz
  docs/{RESTORE.md, TECHNIQUE_TO_FILENAME_REFERENCE.md, *_mappings.py}
  VERSION
videos/<stem>.mov                 <- masters, metadata: source-etag, source-last-modified, stem
```

Manifest columns: `art, scroll, technique_index, technique_number, technique_name,
variation_index, stem, hls_url, source_key, source_size_bytes, source_etag,
source_last_modified, status`. `technique_index` is the 0-based position in the scroll's
`map.Items` (the offset file-name-decipher uses); `technique_number` is the site-facing
`Number` field; `technique_name` is `Name` (Aikido, Danzan Ryu) or `Techniques` (Battodo).
Rows are sorted by art, scroll, technique_index, variation_index; orphan rows follow the
mapped rows of their art. `RESTORE.md` documents every column for a reader with no other
context.

## Deploying

1. **Bucket (once):**
   ```bash
   aws cloudformation deploy --template-file infra/archive-bucket.yaml \
     --stack-name suigetsukan-curriculum-archive --region us-west-2 --profile tennis@suigetsukan
   aws s3api get-object-lock-configuration --bucket suigetsukan-curriculum-archive \
     --region us-west-2 --profile tennis@suigetsukan   # expect GOVERNANCE / 365 days
   ```
2. **Secrets:** `SOURCE_VIDEO_BUCKET`, `ARCHIVE_BUCKET`, `ARCHIVE_BUCKET_REGION` as GitHub
   org secrets (`scripts/org-secrets.env` + `scripts/set_org_secrets.sh`); `SNS_SUPPORT_TOPIC_ARN`
   is shared with cognito-backup. `DEPLOYED_GIT_SHA` comes from the pipeline itself.
3. **Lambda:** merged to `main` → `pipeline.yml` deploys `suigetsukan-curriculum-archive`
   (role `suigetsukan-curriculum-archive-role` with S3 / DynamoDB / SNS managed policies from
   `deploy_roles.py` service discovery; `AmazonS3FullAccess` is not region-scoped so no extra
   policy is needed for the cross-region copy). The reference doc is packaged via the
   `bundle_files` key in `config.json`, which `deploy_lambdas.py` copies into `bundle/`.

## Seeding and pulling

**Seed (once, after the bucket exists):** copies the 22.8 GB server-side so the Lambda's
first runs are not spent catching up.

```bash
./scripts/seed_curriculum_archive.sh
```

Prints before/after object counts; the archive `videos/` count should equal the source
count minus junk keys. Objects seeded by `aws s3 sync` carry no `source-etag` metadata, but
`aws s3 sync` reproduces the source ETag when the multipart part size matches (it does for
this bucket's CLI-uploaded masters), and the Lambda accepts an own-ETag match as in sync.
Any object whose ETag differs is re-copied once by the Lambda, with metadata, and never
again.

**First manual run:**

```bash
aws lambda invoke --function-name suigetsukan-curriculum-archive --region us-west-1 \
  --profile tennis@suigetsukan --cli-read-timeout 900 /tmp/archive-out.json && cat /tmp/archive-out.json
```

**Pull to a local drive** (the "AWS goes away" exit):

```bash
./scripts/pull_curriculum_archive.sh ~/curriculum-archive            # snapshots/latest only, instant
./scripts/pull_curriculum_archive.sh ~/curriculum-archive --videos   # + Deep Archive bulk restore
```

The `--videos` pass requests a Bulk restore (12–48 h) for every master not already
restored and downloads any that are; re-run it after the wait to fetch the rest.

## Reading the monthly SNS summary

Subject `Curriculum Archive OK - snapshots/<date>` on success, `Curriculum Archive FAILURE`
otherwise. The body lists:

- `Manifest rows by art` — how many rows each art contributed.
- `mapped / missing_source / orphan_source` — the status counts (below).
- `Videos: copied / unchanged / skipped_for_time` — what the sync step did. `copied` is
  normally 0 after the seed; a non-zero value means new or re-uploaded masters.
  `skipped_for_time` > 0 means the run hit the 60-second reserve; the next run continues,
  or invoke by hand to finish sooner.
- `Junk keys` — source keys that failed the master pattern and were not archived.
- `Archive` — object count and total size of current versions.
- Up to 50 keys each for missing / orphan / copied / skipped, when non-empty.

## What a non-zero `missing_source` or `orphan_source` means

Both are **findings, not necessarily bugs**; the manifest keeps the rows so the gap is
visible in the archive itself.

- **`missing_source`** — a technique's variation URL points at `<stem>.m3u8` but there is no
  `<stem>.mov` in the source bucket. Either the master was deleted or renamed after
  transcoding (the HLS rendition may still play on the site), or the URL is stale. Check
  the HLS destination bucket for `<stem>`: if the rendition exists, re-upload or recover the
  master under that stem; if not, fix the table entry (remove or repoint the variation).
  The archive cannot recover that video until a master exists.
- **`orphan_source`** — a master exists that no variation references. Usually an unused
  take, a superseded recording, or a file uploaded under a stem the decipher never mapped
  (e.g. a pattern the scroll regex does not accept). Confirm with
  `docs/TECHNIQUE_TO_FILENAME_REFERENCE.md` whether the stem is valid; if it should be
  linked, replay the file through the video pipeline (or publish a `Direct` SNS message with
  its HLS URL) so file-name-decipher writes the variation; if it is dead weight, leave it —
  it is archived regardless and costs nothing in Deep Archive.

Duplicate ETags across different stems are expected: one clip deliberately reused for
several techniques.

## Tests

`tests/test_curriculum_archive.py` covers the manifest (shared clip under two stems, a
`missing_source` row, an `orphan_source` row, junk `.movv` exclusion), the copy decision
(skip on ETag match, copy on mismatch), the time-budget stop, the SNS failure path, the
stem rule's agreement with `file-name-decipher/utils.get_stub`, and that every file the
Lambda uploads is actually in the package.
