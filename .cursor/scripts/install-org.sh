#!/usr/bin/env bash
# Install cursor-settings into every repo in a GitHub organization.
# Requires: gh (GitHub CLI), install.sh from this repo.
#
# Usage:
#   install-org.sh <org> [--no-push] [--no-pr]
#
# Example:
#   ~/src/cursor-settings/scripts/install-org.sh chicken-coop-door-status
#
# Clones (or uses existing) org repos into /tmp/<org>/<repo> with shallow sparse
# checkout (depth 1, only .cursor in working tree). Skips repos whose .cursor
# already matches current cursor-settings; for the rest, runs install.sh then
# commits and pushes .cursor changes. Use --no-push to skip commit/push.
#
# If a repo already has our .cursor install but it has diverged from master,
# opens a PR to the cursor-settings repo proposing those changes (before
# overwriting). Use --no-pr to skip opening PRs.

set -e

CURSOR_SETTINGS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_SCRIPT="$CURSOR_SETTINGS_ROOT/scripts/install.sh"
BASE_DIR="/tmp"
MARKER=".cursor-settings"

ORG=""
PUSH=true
OPEN_PR=true
for arg in "$@"; do
  if [ "$arg" = "--no-push" ]; then
    PUSH=false
  elif [ "$arg" = "--no-pr" ]; then
    OPEN_PR=false
  elif [ -z "$ORG" ]; then
    ORG="$arg"
  fi
done

if [ -z "$ORG" ]; then
  echo "Usage: install-org.sh <org> [--no-push] [--no-pr]"
  exit 1
fi

if [ ! -f "$INSTALL_SCRIPT" ]; then
  echo "error: install.sh not found at $INSTALL_SCRIPT"
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "error: gh (GitHub CLI) required. Install: brew install gh"
  exit 1
fi

echo "Org: $ORG"
echo "Base dir: $BASE_DIR"
[ "$PUSH" = false ] && echo "Push: disabled (--no-push)"
[ "$OPEN_PR" = false ] && echo "PR: disabled (--no-pr)"
echo ""

mkdir -p "$BASE_DIR/$ORG"
cd "$BASE_DIR/$ORG"

repos=$(gh repo list "$ORG" --json name,isArchived -q '.[] | select(.isArchived == false) | .name' 2>/dev/null) || {
  echo "error: failed to list repos (check org name and gh auth)"
  exit 1
}

count=0
for repo in $repos; do
  dir="$BASE_DIR/$ORG/$repo"
  if [ ! -d "$dir" ]; then
    echo "Cloning $ORG/$repo (shallow, sparse for .cursor only)..."
    git clone --depth 1 --no-checkout "https://github.com/$ORG/$repo.git" "$dir"
    if ! git -C "$dir" rev-parse HEAD 2>/dev/null; then
      echo "  Empty repo, skipping"
      rm -rf "$dir"
      ((count++)) || true
      continue
    fi
    default_branch=$(git -C "$dir" symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|^refs/remotes/origin/||') || default_branch="main"
    git -C "$dir" sparse-checkout init --cone
    git -C "$dir" sparse-checkout set .cursor
    git -C "$dir" checkout "$default_branch"
  fi
  echo "Installing into $ORG/$repo..."

  # Skip install/push if repo's .cursor already matches current cursor-settings
  if [ -d "$dir/.cursor" ] && [ -f "$dir/.cursor/$MARKER" ]; then
    if diff -rq -x '.git' "$dir/.cursor" "$CURSOR_SETTINGS_ROOT" >/dev/null 2>&1; then
      echo "  Already in sync, skipping"
      ((count++)) || true
      continue
    fi
  fi

  # If repo has our .cursor install and it diverged from master, open a PR to propose changes
  if [ "$OPEN_PR" = true ] && [ -d "$dir/.cursor" ] && [ -f "$dir/.cursor/$MARKER" ]; then
    if ! diff -rq -x '.git' "$dir/.cursor" "$CURSOR_SETTINGS_ROOT" >/dev/null 2>&1; then
      echo "  Diverged from master; opening PR..."
      from_org_branch="from-org/${ORG}-${repo}"
      from_org_branch="${from_org_branch//\//-}"
      cursor_origin=$(git -C "$CURSOR_SETTINGS_ROOT" remote get-url origin 2>/dev/null) || true
      if [ -n "$cursor_origin" ]; then
        pr_worktree="/tmp/cursor-settings-pr-$$"
        git clone --depth 1 "$cursor_origin" "$pr_worktree" 2>/dev/null || true
        if [ -d "$pr_worktree/.git" ]; then
          git -C "$pr_worktree" checkout -b "$from_org_branch"
          rsync -a --exclude='.git' "$dir/.cursor/" "$pr_worktree/"
          if [ -n "$(git -C "$pr_worktree" status --short)" ]; then
            git -C "$pr_worktree" add -A
            git -C "$pr_worktree" commit -m "Propose .cursor changes from $ORG/$repo"
            pr_repo_url=$(git -C "$pr_worktree" remote get-url origin)
            pr_repo_slug=$(echo "$pr_repo_url" | sed -E 's#^(https?://[^/]+/|git@[^:]+:)##' | sed 's#\.git$##')
            if git -C "$pr_worktree" push -u origin "$from_org_branch" 2>/dev/null; then
              if (cd "$CURSOR_SETTINGS_ROOT" && gh pr create --repo "$pr_repo_slug" \
                --head "$from_org_branch" \
                --title "Propose .cursor changes from $ORG/$repo" \
                --body "Changes in .cursor from $ORG/$repo (proposed for master)."); then
                echo "  Opened PR from branch $from_org_branch"
              else
                echo "  PR creation failed (continuing anyway)"
              fi
            else
              echo "  Push failed (no write access?); PR not created"
            fi
          fi
          rm -rf "$pr_worktree"
        fi
        rm -rf "$pr_worktree" 2>/dev/null || true
      else
        echo "  cursor-settings has no remote origin; cannot open PR"
      fi
    fi
  fi

  "$INSTALL_SCRIPT" "$dir"
  if [ "$PUSH" = true ] && git -C "$dir" rev-parse --git-dir >/dev/null 2>&1; then
    if [ -n "$(git -C "$dir" status --short .cursor 2>/dev/null)" ]; then
      git -C "$dir" add .cursor
      git -C "$dir" commit -m "chore: update .cursor from cursor-settings"
      git -C "$dir" push
      echo "  Pushed .cursor changes"
    fi
  fi
  ((count++)) || true
done

echo ""
echo "Done. Installed into $count repo(s)."
