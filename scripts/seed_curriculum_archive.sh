#!/usr/bin/env bash
#
# seed_curriculum_archive.sh - One-shot initial copy of every source video master into
# the curriculum archive bucket (videos/ prefix), server-side and cross-region.
#
# Run by hand after infra/archive-bucket.yaml is deployed and before (or alongside) the
# first suigetsukan-curriculum-archive Lambda run. The Lambda would otherwise spend its
# first several monthly runs copying ~22.8 GB inside its 15-minute budget.
#
# The source bucket tiers every tagged .mov into DEEP_ARCHIVE after 90 days, and an
# archived object cannot be read or copied until it has been restored (Bulk: up to 48 h).
# So this is a TWO-PASS operation:
#   pass 1  requests a Bulk restore for every archived master (and copies any that are
#           already restored or still STANDARD);
#   pass 2  12-48 h later, copies the now-restored masters.
# Re-run until the tally shows no restore_requested / awaiting_restore. Every copy is made
# with `s3api copy-object` and carries the same source-etag / source-last-modified / stem
# metadata the Lambda writes, so the Lambda's next run treats it as already in sync.
#
# Only keys shaped like a real master (^[abd][a-z0-9]+\.mov$) are considered; junk keys
# such as `bi01ca.movv` are left behind, matching the Lambda. The archive is never pruned.
#
# Usage:
#   ./scripts/seed_curriculum_archive.sh
#
# Environment overrides (defaults match production):
#   AWS_PROFILE_NAME     tennis@suigetsukan
#   SOURCE_VIDEO_BUCKET  suigetsukan-curriculum-video-proce-source71e471f1-ktb4onibtuze
#   SOURCE_REGION        us-west-1
#   ARCHIVE_BUCKET       suigetsukan-curriculum-archive
#   ARCHIVE_REGION       us-west-2
#   RESTORE_DAYS         40    (matches the Lambda: long enough for its next monthly run)
#   RESTORE_TIER         Bulk  (Standard = up to 12 h at higher cost)
#   PARALLEL             8     (concurrent AWS CLI calls)

set -euo pipefail

export PROFILE="${AWS_PROFILE_NAME:-tennis@suigetsukan}"
export SOURCE_BUCKET="${SOURCE_VIDEO_BUCKET:-suigetsukan-curriculum-video-proce-source71e471f1-ktb4onibtuze}"
export SOURCE_REGION="${SOURCE_REGION:-us-west-1}"
export ARCHIVE_BUCKET="${ARCHIVE_BUCKET:-suigetsukan-curriculum-archive}"
export ARCHIVE_REGION="${ARCHIVE_REGION:-us-west-2}"
export RESTORE_DAYS="${RESTORE_DAYS:-40}"
export RESTORE_TIER="${RESTORE_TIER:-Bulk}"
PARALLEL="${PARALLEL:-8}"
export VIDEO_PREFIX="videos/"

count_archive_videos() {
  # shellcheck disable=SC2016  # backticks are JMESPath literal syntax, not shell
  aws s3api list-objects-v2 --bucket "$ARCHIVE_BUCKET" --prefix "$VIDEO_PREFIX" \
    --region "$ARCHIVE_REGION" --profile "$PROFILE" \
    --query 'length(Contents || `[]`)' --output text
}

# One master per line: key <TAB> etag <TAB> last-modified <TAB> storage-class
list_source_masters() {
  aws s3api list-objects-v2 --bucket "$SOURCE_BUCKET" \
    --region "$SOURCE_REGION" --profile "$PROFILE" --output json \
    | python3 -c '
import json, re, sys
pattern = re.compile(r"^[abd][a-z0-9]+\.mov$")
for obj in json.load(sys.stdin).get("Contents", []):
    if pattern.match(obj["Key"]):
        print("\t".join([obj["Key"], obj["ETag"].strip("\""), obj["LastModified"],
                         obj.get("StorageClass", "STANDARD")]))
'
}

# seed_one <key> <etag> <last-modified> <storage-class>  -> prints "<outcome> <key>"
seed_one() {
  local key="$1" etag="$2" last_modified="$3" storage_class="$4"
  local stem="${key%.mov}" stored restore
  stored="$(aws s3api head-object --bucket "$ARCHIVE_BUCKET" --key "$VIDEO_PREFIX$key" \
    --region "$ARCHIVE_REGION" --profile "$PROFILE" \
    --query 'Metadata."source-etag"' --output text 2>/dev/null || true)"
  if [ "$stored" = "$etag" ]; then
    echo "unchanged $key"
    return
  fi
  if [ "$storage_class" = "GLACIER" ] || [ "$storage_class" = "DEEP_ARCHIVE" ]; then
    restore="$(aws s3api head-object --bucket "$SOURCE_BUCKET" --key "$key" \
      --region "$SOURCE_REGION" --profile "$PROFILE" --query 'Restore' --output text)"
    case "$restore" in
      *'ongoing-request="false"'*) ;;  # restored copy available: fall through and copy
      *'ongoing-request="true"'*)
        echo "awaiting_restore $key"
        return
        ;;
      *)
        if aws s3api restore-object --bucket "$SOURCE_BUCKET" --key "$key" \
          --region "$SOURCE_REGION" --profile "$PROFILE" \
          --restore-request "{\"Days\":$RESTORE_DAYS,\"GlacierJobParameters\":{\"Tier\":\"$RESTORE_TIER\"}}" \
          >/dev/null 2>&1; then
          echo "restore_requested $key"
        else
          echo "failed $key (restore-object)"
        fi
        return
        ;;
    esac
  fi
  if aws s3api copy-object --bucket "$ARCHIVE_BUCKET" --key "$VIDEO_PREFIX$key" \
    --copy-source "$SOURCE_BUCKET/$key" --metadata-directive REPLACE \
    --content-type video/quicktime \
    --metadata "source-etag=$etag,source-last-modified=$last_modified,stem=$stem" \
    --region "$ARCHIVE_REGION" --profile "$PROFILE" >/dev/null 2>&1; then
    echo "copied $key"
  else
    echo "failed $key (copy-object)"
  fi
}
export -f seed_one

echo "Seeding s3://$ARCHIVE_BUCKET/$VIDEO_PREFIX ($ARCHIVE_REGION) from s3://$SOURCE_BUCKET ($SOURCE_REGION)"
echo "Profile: $PROFILE  restore: $RESTORE_TIER tier, $RESTORE_DAYS days  parallel: $PARALLEL"
echo

listing="$(mktemp)"
results="$(mktemp)"
trap 'rm -f "$listing" "$results"' EXIT

list_source_masters > "$listing"
source_masters="$(wc -l < "$listing" | tr -d ' ')"
archive_before="$(count_archive_videos)"
echo "Before: source masters matching pattern=$source_masters; archive videos/=$archive_before"
echo "Working (one line per master)..."

xargs -P "$PARALLEL" -L 1 bash -c 'seed_one "$@"' _ < "$listing" | tee "$results" | grep -Ev '^unchanged ' || true

echo
archive_after="$(count_archive_videos)"
tally() { grep -c "^$1 " "$results" || true; }
copied="$(tally copied)"
unchanged="$(tally unchanged)"
requested="$(tally restore_requested)"
awaiting="$(tally awaiting_restore)"
failed="$(tally failed)"
echo "Tally: copied=$copied unchanged=$unchanged restore_requested=$requested awaiting_restore=$awaiting failed=$failed"
echo "After:  source masters matching pattern=$source_masters; archive videos/=$archive_after"

if [ "$failed" -gt 0 ]; then
  echo "ERROR: $failed master(s) failed; see the lines above." >&2
  exit 2
fi
pending=$((requested + awaiting))
if [ "$pending" -gt 0 ]; then
  echo
  echo "$pending master(s) are being restored from Deep Archive ($RESTORE_TIER tier: up to 48 h)."
  echo "Re-run this script after the wait to copy them:"
  echo "  $0"
  exit 0
fi
if [ "$archive_after" -lt "$source_masters" ]; then
  echo "WARNING: archive videos/ count ($archive_after) is below the source master count ($source_masters)." >&2
  exit 2
fi
echo "OK: archive videos/ holds every source master (expected = source count minus junk keys)."
