#!/usr/bin/env bash
#
# seed_curriculum_archive.sh - One-shot initial copy of every source video master into
# the curriculum archive bucket (videos/ prefix), server-side and cross-region.
#
# Run by hand ONCE after infra/archive-bucket.yaml is deployed and before (or alongside)
# the first suigetsukan-curriculum-archive Lambda run. The Lambda would otherwise spend
# its first several monthly runs copying ~22.8 GB inside its 15-minute budget.
#
# Only keys shaped like a real master ([abd]*.mov) are copied; junk keys such as
# `bi01ca.movv` are left behind, matching the Lambda's VIDEO_KEY_PATTERN. The archive is
# never pruned: `aws s3 sync` without --delete adds and updates only.
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

set -euo pipefail

PROFILE="${AWS_PROFILE_NAME:-tennis@suigetsukan}"
SOURCE_BUCKET="${SOURCE_VIDEO_BUCKET:-suigetsukan-curriculum-video-proce-source71e471f1-ktb4onibtuze}"
SOURCE_REGION="${SOURCE_REGION:-us-west-1}"
ARCHIVE_BUCKET="${ARCHIVE_BUCKET:-suigetsukan-curriculum-archive}"
ARCHIVE_REGION="${ARCHIVE_REGION:-us-west-2}"
VIDEO_PREFIX="videos/"

count_objects() {
  # count_objects <bucket> <region> [prefix]
  local bucket="$1" region="$2" prefix="${3:-}"
  # shellcheck disable=SC2016  # backticks are JMESPath literal syntax, not shell
  aws s3api list-objects-v2 --bucket "$bucket" --prefix "$prefix" \
    --region "$region" --profile "$PROFILE" \
    --query 'length(Contents || `[]`)' --output text
}

count_masters() {
  # Source keys that the Lambda's pattern accepts (^[abd][a-z0-9]+\.mov$)
  aws s3api list-objects-v2 --bucket "$SOURCE_BUCKET" \
    --region "$SOURCE_REGION" --profile "$PROFILE" \
    --query 'Contents[].Key' --output text | tr '\t' '\n' \
    | grep -Ec '^[abd][a-z0-9]+\.mov$' || true
}

echo "Seeding s3://$ARCHIVE_BUCKET/$VIDEO_PREFIX ($ARCHIVE_REGION) from s3://$SOURCE_BUCKET ($SOURCE_REGION)"
echo "Profile: $PROFILE"
echo

source_total="$(count_objects "$SOURCE_BUCKET" "$SOURCE_REGION")"
source_masters="$(count_masters)"
archive_before="$(count_objects "$ARCHIVE_BUCKET" "$ARCHIVE_REGION" "$VIDEO_PREFIX")"
echo "Before: source objects=$source_total (masters matching pattern=$source_masters); archive videos/=$archive_before"
echo

aws s3 sync "s3://$SOURCE_BUCKET" "s3://$ARCHIVE_BUCKET/$VIDEO_PREFIX" \
  --exclude '*' --include '[abd]*.mov' \
  --source-region "$SOURCE_REGION" --region "$ARCHIVE_REGION" \
  --profile "$PROFILE" --no-progress

echo
archive_after="$(count_objects "$ARCHIVE_BUCKET" "$ARCHIVE_REGION" "$VIDEO_PREFIX")"
echo "After:  source objects=$source_total (masters matching pattern=$source_masters); archive videos/=$archive_after"

if [ "$archive_after" -lt "$source_masters" ]; then
  echo "WARNING: archive videos/ count ($archive_after) is below the source master count ($source_masters)." >&2
  echo "         Re-run this script, or let the Lambda's monthly sync catch up." >&2
  exit 2
fi
echo "OK: archive videos/ holds every source master (expected = source count minus junk keys)."
