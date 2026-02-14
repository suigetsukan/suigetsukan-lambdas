# Bats tests for scripts/install.sh
# Run: bats tests/install.bats

setup() {
  SCRIPT_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
  INSTALL_SCRIPT="$SCRIPT_DIR/scripts/install.sh"
}

@test "fails when target is not a git repository" {
  run env -i PATH="$PATH" "$INSTALL_SCRIPT" /tmp
  [ "$status" -eq 1 ]
  [[ "$output" == *"not a git repository"* ]]
}

@test "fails when .cursor is a symlink" {
  tmp=$(mktemp -d)
  cd "$tmp"
  git init -q
  ln -s /tmp .cursor
  run "$INSTALL_SCRIPT" "$tmp"
  cd /
  rm -rf "$tmp"
  [ "$status" -eq 1 ]
  [[ "$output" == *"symlink"* ]]
}

@test "fails when run from within cursor-settings" {
  run "$INSTALL_SCRIPT" "$SCRIPT_DIR"
  [ "$status" -eq 1 ]
  [[ "$output" == *"do not run install.sh from within cursor-settings"* ]]
}

@test "fails when .cursor exists as plain directory" {
  tmp=$(mktemp -d)
  cd "$tmp"
  git init -q
  mkdir .cursor
  run "$INSTALL_SCRIPT" "$tmp"
  cd /
  rm -rf "$tmp"
  [ "$status" -eq 1 ]
  [[ "$output" == *"already exists"* ]]
}

@test "exits 0 when submodule already present" {
  # Create bare clone of this repo (avoids external URLs)
  bare=$(mktemp -d)
  git clone --bare -q "$SCRIPT_DIR" "$bare"

  # Git 2.39+ forbids file:// by default; allow for this test
  saved=$(git config --global protocol.file.allow 2>/dev/null || echo "unset")
  git config --global protocol.file.allow always

  tmp=$(mktemp -d)
  cd "$tmp"
  git init -q
  git config user.email "test@test"
  git config user.name "Test"
  CURSOR_SETTINGS_REPO="file://$bare" "$INSTALL_SCRIPT" "$tmp"
  [ -d .cursor ]

  output=$(CURSOR_SETTINGS_REPO="file://$bare" "$INSTALL_SCRIPT" "$tmp" 2>&1)
  status=$?

  # Restore protocol.file.allow
  if [ "$saved" = "unset" ]; then
    git config --global --unset protocol.file.allow 2>/dev/null || true
  else
    git config --global protocol.file.allow "$saved"
  fi

  cd /
  rm -rf "$tmp" "$bare"
  [ "$status" -eq 0 ]
  [[ "$output" == *"already present"* ]]
}
