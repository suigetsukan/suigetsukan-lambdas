#!/usr/bin/env bash
#
# pull_curriculum_archive.sh - Pull the curriculum archive to a local drive. This is the
# "AWS goes away" exit: everything needed to read and republish the curriculum lands in
# one folder (see RESTORE.md inside it).
#
# Usage:
#   ./scripts/pull_curriculum_archive.sh <local-dir>            # snapshots/latest/ only (instant)
#   ./scripts/pull_curriculum_archive.sh <local-dir> --videos   # also request / download videos/
#
# videos/ lives in S3 Glacier Deep Archive. The first --videos run issues a Bulk restore
# request for every object that is not already restored (12-48 h, cheapest tier) and
# downloads whatever is already available; re-run with --videos after the wait to fetch
# the rest. Restored copies stay readable for RESTORE_DAYS days.
#
# Environment overrides (defaults match production):
#   AWS_PROFILE_NAME  tennis@suigetsukan
#   ARCHIVE_BUCKET    suigetsukan-curriculum-archive
#   ARCHIVE_REGION    us-west-2
#   RESTORE_DAYS      14
#   RESTORE_TIER      Bulk   (Standard = ~12 h at higher cost)

set -euo pipefail

PROFILE="${AWS_PROFILE_NAME:-tennis@suigetsukan}"
ARCHIVE_BUCKET="${ARCHIVE_BUCKET:-suigetsukan-curriculum-archive}"
ARCHIVE_REGION="${ARCHIVE_REGION:-us-west-2}"
RESTORE_DAYS="${RESTORE_DAYS:-14}"
RESTORE_TIER="${RESTORE_TIER:-Bulk}"

usage() {
  echo "Usage: $0 <local-dir> [--videos]" >&2
  exit 1
}

LOCAL_DIR="${1:-}"
[ -n "$LOCAL_DIR" ] || usage
WITH_VIDEOS=false
if [ "${2:-}" = "--videos" ]; then
  WITH_VIDEOS=true
elif [ -n "${2:-}" ]; then
  usage
fi

aws_s3api() {
  aws s3api "$@" --region "$ARCHIVE_REGION" --profile "$PROFILE"
}

mkdir -p "$LOCAL_DIR"

echo "== snapshots/latest/ -> $LOCAL_DIR/snapshots/latest/"
aws s3 sync "s3://$ARCHIVE_BUCKET/snapshots/latest/" "$LOCAL_DIR/snapshots/latest/" \
  --region "$ARCHIVE_REGION" --profile "$PROFILE" --no-progress
echo "Snapshot pulled. Start with $LOCAL_DIR/snapshots/latest/docs/RESTORE.md"

$WITH_VIDEOS || exit 0

echo
echo "== videos/ (Deep Archive): checking restore state"
keys_file="$(mktemp)"
trap 'rm -f "$keys_file"' EXIT
aws_s3api list-objects-v2 --bucket "$ARCHIVE_BUCKET" --prefix videos/ \
  --query 'Contents[].Key' --output text | tr '\t' '\n' | sed '/^$/d' > "$keys_file"
total="$(wc -l < "$keys_file" | tr -d ' ')"
echo "videos/ objects: $total"

requested=0
in_progress=0
ready=0
while IFS= read -r key; do
  head="$(aws_s3api head-object --bucket "$ARCHIVE_BUCKET" --key "$key" --output json)"
  storage_class="$(printf '%s' "$head" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("StorageClass",""))')"
  restore="$(printf '%s' "$head" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("Restore",""))')"
  if [ "$storage_class" != "DEEP_ARCHIVE" ] && [ "$storage_class" != "GLACIER" ]; then
    ready=$((ready + 1))
    continue
  fi
  case "$restore" in
    *'ongoing-request="false"'*) ready=$((ready + 1)) ;;
    *'ongoing-request="true"'*) in_progress=$((in_progress + 1)) ;;
    *)
      aws_s3api restore-object --bucket "$ARCHIVE_BUCKET" --key "$key" \
        --restore-request "{\"Days\":$RESTORE_DAYS,\"GlacierJobParameters\":{\"Tier\":\"$RESTORE_TIER\"}}" \
        >/dev/null
      requested=$((requested + 1))
      ;;
  esac
done < "$keys_file"
echo "restore requested now=$requested, already in progress=$in_progress, ready to download=$ready"

if [ "$ready" -gt 0 ]; then
  echo
  echo "== downloading restored videos -> $LOCAL_DIR/videos/"
  aws s3 sync "s3://$ARCHIVE_BUCKET/videos/" "$LOCAL_DIR/videos/" \
    --region "$ARCHIVE_REGION" --profile "$PROFILE" --force-glacier-transfer --no-progress \
    || echo "Some objects are not restored yet; that is expected on the first pass." >&2
fi

pending=$((requested + in_progress))
if [ "$pending" -gt 0 ]; then
  echo
  echo "$pending video(s) are being restored from Deep Archive ($RESTORE_TIER tier: typically 12-48 h)."
  echo "Re-run after the wait to download them:"
  echo "  $0 $LOCAL_DIR --videos"
else
  echo
  echo "All videos downloaded to $LOCAL_DIR/videos/"
fi
