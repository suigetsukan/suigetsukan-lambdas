#!/bin/bash
# migrate_lambda_repos.sh - List and clone suigetsukan lambda repos for consolidation
#
# Usage:
#   ./scripts/migrate_lambda_repos.sh list          # List lambda repos (excludes suigetsukan-lambdas)
#   ./scripts/migrate_lambda_repos.sh clone [dir]   # Clone all into /tmp or specified dir
#   ./scripts/migrate_lambda_repos.sh migrate NAME  # Migrate one repo into lambdas/NAME
#
# Requires: gh CLI (brew install gh), authenticated to suigetsukan org

set -e
ORG="suigetsukan"
WORK_DIR="${WORK_DIR:-/tmp/suigetsukan-lambda-repos}"
TARGET_LAMBDAS="$(cd "$(dirname "$0")/.." && pwd)/lambdas"

list_repos() {
    echo "Fetching suigetsukan repos with 'lambda' in name..."
    gh repo list "$ORG" --limit 100 2>/dev/null | grep -i lambda | grep -v "suigetsukan-lambdas" || true
}

get_repo_names() {
    list_repos | awk '{print $1}' | sed "s|$ORG/||"
}

clone_all() {
    local work="${1:-$WORK_DIR}"
    mkdir -p "$work"
    cd "$work"
    for repo in $(get_repo_names); do
        if [ -d "$repo" ]; then
            echo "Already cloned: $repo"
        else
            echo "Cloning $ORG/$repo..."
            gh repo clone "$ORG/$repo" "$repo" 2>/dev/null || echo "  (skip: clone failed)"
        fi
    done
    echo "Cloned repos in: $WORK_DIR"
}

# Derive short lambda name from repo: suigetsukan-curriculum-X-lambda -> X
repo_to_lambda_name() {
    local repo="$1"
    echo "$repo" | sed 's/^suigetsukan-curriculum-//' | sed 's/-lambda$//'
}

migrate_one() {
    local repo_name="$1"
    if [ -z "$repo_name" ]; then
        echo "Usage: $0 migrate <repo-name>"
        echo "Example: $0 migrate suigetsukan-curriculum-file-name-decipher-lambda"
        exit 1
    fi

    local lambda_name
    lambda_name=$(repo_to_lambda_name "$repo_name")
    local src="${WORK_DIR}/${repo_name}"
    local dst="${TARGET_LAMBDAS}/${lambda_name}"

    if [ ! -d "$src" ]; then
        echo "Repo not cloned. Run: $0 clone"
        echo "Then: gh repo clone $ORG/$repo_name $WORK_DIR/$repo_name"
        exit 1
    fi

    mkdir -p "$dst"

    # Copy main handler (common patterns)
    local handler_src=""
    if [ -f "$src/lambda_function.py" ]; then
        cp "$src/lambda_function.py" "$dst/app.py"
        handler_src="lambda_function.py"
    elif [ -f "$src/handler.py" ]; then
        cp "$src/handler.py" "$dst/app.py"
        handler_src="handler.py"
    elif [ -f "$src/app.py" ]; then
        cp "$src/app.py" "$dst/app.py"
        handler_src="app.py"
    else
        local handler_py first_py
        handler_py=$(grep -l "def lambda_handler\|def handler" "$src"/*.py 2>/dev/null | head -1)
        first_py=$(find "$src" -maxdepth 1 -name "*.py" -type f ! -path "*/\.*" | head -1)
        if [ -n "$handler_py" ]; then
            cp "$handler_py" "$dst/app.py"
            handler_src=$(basename "$handler_py")
        elif [ -n "$first_py" ]; then
            cp "$first_py" "$dst/app.py"
            handler_src=$(basename "$first_py")
        else
            echo "No Python handler found in $src"
            exit 1
        fi
    fi

    # Copy requirements
    [ -f "$src/requirements.txt" ] && cp "$src/requirements.txt" "$dst/"
    [ ! -f "$dst/requirements.txt" ] && echo "boto3" > "$dst/requirements.txt"

    # Copy other Python files (utils, etc.) - exclude the handler we already copied as app.py
    for py in "$src"/*.py; do
        [ -f "$py" ] && [ "$(basename "$py")" != "$handler_src" ] && cp "$py" "$dst/"
    done 2>/dev/null || true

    # Copy subdirs (util, etc.) if any
    for subdir in util helpers lib; do
        [ -d "$src/$subdir" ] && cp -r "$src/$subdir" "$dst/"
    done

    # Generate config.json if not present
    if [ ! -f "$dst/config.json" ]; then
        cat > "$dst/config.json" << EOF
{
  "function_name_suffix": "$lambda_name",
  "function_name": "suigetsukan-$lambda_name",
  "handler": "app.lambda_handler",
  "runtime": "python3.12",
  "timeout": 300,
  "memory_size": 256,
  "env_vars": {},
  "layers": [],
  "tags": {"Project": "suigetsukan-curriculum", "Environment": "prod"},
  "role_name": "suigetsukan-$lambda_name-role",
  "exclude_files": ["*.pyc", "__pycache__/*", "tests/**"]
}
EOF
        echo "Generated $dst/config.json - REVIEW env_vars and handler!"
    fi

    echo "Migrated $repo_name -> $dst"
}

case "${1:-list}" in
    list)   list_repos ;;
    clone)  clone_all "$2" ;;
    migrate) migrate_one "$2" ;;
    *)      echo "Usage: $0 {list|clone|migrate [repo-name]}"; exit 1 ;;
esac
