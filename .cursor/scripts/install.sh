#!/usr/bin/env bash
# Install cursor-settings into .cursor. If already installed (has .cursor-settings marker),
# rsync updates; otherwise backs up existing .cursor and does fresh install.
#
# Usage:
#   cd your-project && ~/src/cursor-settings/scripts/install.sh
#   cd your-project && /path/to/cursor-settings/scripts/install.sh [target-dir]

set -e

CURSOR_SETTINGS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="$(cd "${1:-.}" && pwd)"
MARKER=".cursor-settings"

cd "$TARGET_DIR"

# Don't install into cursor-settings itself
if [ "$TARGET_DIR" = "$CURSOR_SETTINGS_ROOT" ]; then
  echo "error: do not run install.sh from within cursor-settings; run from a target project"
  exit 1
fi

# Sync repo into .cursor. Exclude .git and tests/ (tests are for cursor-settings dev/CI only, not for installed copy).
if [ -e .cursor ] && [ -f .cursor/"$MARKER" ]; then
  # Already our install: rsync to update, leave user additions intact
  rsync -a --exclude='.git' --exclude='tests' "$CURSOR_SETTINGS_ROOT/" .cursor/
  echo "Updated cursor-settings in .cursor"
else
  # Fresh install or unknown .cursor
  if [ -e .cursor ]; then
    [ -e d0t_cursor_0ld ] && rm -rf d0t_cursor_0ld
    mv .cursor d0t_cursor_0ld
    echo "Moved existing .cursor to d0t_cursor_0ld"
  fi
  mkdir -p .cursor
  rsync -a --exclude='.git' --exclude='tests' "$CURSOR_SETTINGS_ROOT/" .cursor/
  echo "Installed cursor-settings to .cursor"
fi
