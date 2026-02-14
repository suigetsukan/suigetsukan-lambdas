#!/usr/bin/env bash
# Find and optionally remove bats-core/bats-action from workflow files in every repo in an org.
# Requires: gh (GitHub CLI), with auth that can read repo contents and optionally push.
#
# Usage:
#   remove-bats-from-org-repos.sh <org>              # report which repos have bats in workflows
#   remove-bats-from-org-repos.sh <org> --fix        # clone, remove bats job, commit & push
#   remove-bats-from-org-repos.sh --all-orgs         # use .github/orgs-to-update.txt for org list (report only)
#   remove-bats-from-org-repos.sh --all-orgs --fix   # fix all orgs from orgs-to-update.txt
#
# The "bats" job is removed entirely from any workflow file that references bats-core/bats-action.

set -e

CURSOR_SETTINGS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORG_LIST="$CURSOR_SETTINGS_ROOT/.github/orgs-to-update.txt"
FIX=false
USE_ORG_FILE=false
ORGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fix)       FIX=true; shift ;;
    --all-orgs) USE_ORG_FILE=true; shift ;;
    *)          ORGS+=("$1"); shift ;;
  esac
done

if [[ "$USE_ORG_FILE" == true ]]; then
  ORGS=()
  while IFS= read -r line; do
    [[ "$line" =~ ^#.*$ || -z "${line// }" ]] && continue
    ORGS+=("$line")
  done < "$ORG_LIST"
fi

if [[ ${#ORGS[@]} -eq 0 ]]; then
  echo "Usage: $0 <org> [--fix]   OR   $0 --all-orgs [--fix]"
  echo "  Report (default) or remove bats from .github/workflows in every repo in the org(s)."
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "error: gh (GitHub CLI) required. Install: brew install gh"
  exit 1
fi

# Remove the job block that contains "bats-core" or "bats-action" (typically install-script).
# Reads stdin, writes stdout. Skips from "  install-script:" (or any job line before bats) until next "  jobname:".
remove_bats_job() {
  awk '
    /^  [a-zA-Z0-9_-]+:\s*$/ {
      if (skip) { skip = 0; print; next }
      if ($0 ~ /install-script/) { skip = 1; next }
    }
    /bats-core|bats-action/ { skip = 1; next }
    !skip { print }
  '
}

# Check one repo for bats in workflow files; return 0 if found.
repo_has_bats() {
  local org="$1" repo="$2"
  local list
  list=$(gh api "repos/${org}/${repo}/contents/.github/workflows" 2>/dev/null) || return 1
  echo "$list" | jq -r '.[].name' 2>/dev/null | while read -r f; do
    [[ -z "$f" ]] && continue
    content=$(gh api "repos/${org}/${repo}/contents/.github/workflows/${f}?ref=HEAD" --jq '.content' 2>/dev/null | base64 -d 2>/dev/null) || continue
    if echo "$content" | grep -q 'bats-core\|bats-action'; then
      echo "$f"
      return 0
    fi
  done
  return 1
}

# Fix one repo: clone, remove bats from workflow file(s), commit and push.
fix_repo() {
  local org="$1" repo="$2" tmpdir
  tmpdir=$(mktemp -d)
  trap "rm -rf '$tmpdir'" RETURN
  git clone --depth 1 "https://github.com/${org}/${repo}.git" "$tmpdir" 2>/dev/null || return 1
  cd "$tmpdir"
  local modified=0
  for wf in .github/workflows/*.yml .github/workflows/*.yaml; do
    [[ -f "$wf" ]] || continue
    if grep -q 'bats-core\|bats-action' "$wf"; then
      remove_bats_job < "$wf" > "${wf}.new" && mv "${wf}.new" "$wf"
      modified=1
    fi
  done
  if [[ $modified -eq 1 ]]; then
    git add .github/workflows/
    git commit -m "chore: remove bats from workflow (cursor-settings uses pytest)"
    git push
    echo "  Pushed fix to ${org}/${repo}"
  fi
}

for org in "${ORGS[@]}"; do
  echo "Org: $org"
  repos=$(gh repo list "$org" --json name,isArchived -q '.[] | select(.isArchived == false) | .name' 2>/dev/null) || {
    echo "  (failed to list repos)"
    continue
  }
  for repo in $repos; do
    files=$(repo_has_bats "$org" "$repo") || continue
    for wf in $files; do
      echo "  ${org}/${repo}  .github/workflows/${wf}"
    done
    if [[ "$FIX" == true ]]; then
      fix_repo "$org" "$repo" || echo "  (fix failed for ${org}/${repo})"
    fi
  done
done
