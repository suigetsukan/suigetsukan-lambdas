# cursor-settings

Shared [Cursor](https://cursor.com) IDE rules for all development work. Use as a **Git submodule** in projects.

## Overview: how the whole process works

This repo is the **single source of truth** for shared Cursor rules and config. Projects use a copy of it in a **`.cursor`** directory (either as a Git submodule or via the installer, which rsyncs this repo into `.cursor`).

- **One project:** Run `scripts/install.sh` from the project (or point it at a path). It syncs this repo into that project’s `.cursor`. Re-run anytime to pull latest rules.
- **Many projects (org-wide):** Run `scripts/install-org.sh <org>`. It uses the GitHub API to list every repo in the org, clones each with a **shallow, sparse checkout** (only the `.cursor` directory is checked out), and for each repo either skips it (if `.cursor` already matches this repo) or runs `install.sh` and then commits and pushes only the `.cursor` tree. So only `.cursor` is ever touched in org repos.
- **Automation:** The Pipeline workflow (`.github/workflows/pipeline.yml`) runs on every push and PR. On push it also runs the reconcile job, which reads `.github/orgs-to-update.txt` and runs `install-org.sh` for each org, keeping orgs’ repos in sync with this repo.
- **Flow back:** If an org repo’s `.cursor` has diverged from this repo (e.g. someone edited rules there), the script opens a PR to this repo proposing those changes before overwriting, so improvements can be merged into the single source of truth.

All logic lives in the scripts; the workflow just invokes them. The sections below give the exact commands and options.

## What This Is

- **`rules/`** — Rule files (`.mdc`) that Cursor applies when you edit code
- Rules are compiled from `AGENTS.md` across ~/src projects (C2DS-*, gecl-app-common, GGG-*, etc.)
- `alwaysApply: true` rules apply to every session; file-specific rules apply when matching files are open

## Using as a Submodule

Assumes cursor-settings and your projects are **private repos**. SSH access must be configured for both.

### One-time: Add to a project

From your project directory:

```bash
cd ~/src/your-project
git submodule add git@github.com:gamename/cursor-settings.git .cursor
git add .gitmodules .cursor
git commit -m "Add cursor-settings as submodule"
git push
```

If your fork uses a different org, replace `gamename` with yours.
`git submodule add` clones cursor-settings into `.cursor` — no separate clone needed.

**Optional:** If you have cursor-settings cloned, use the installer — it auto-detects the repo URL from your local clone:

```bash
cd ~/src/your-project
~/src/cursor-settings/scripts/install.sh
# Or: ~/src/cursor-settings/scripts/install.sh /path/to/other-project
git commit -m "Add cursor-settings as submodule"
git push
```

The submodule root becomes the project's `.cursor` directory, so Cursor finds `rules/` at the expected path.

### Org-wide install

To install into every repo in a GitHub org (e.g. for a team): `scripts/install-org.sh <org>`. Requires `gh` (GitHub CLI). Clones each org repo into `/tmp/<org>/<repo>`, runs `install.sh`, then commits and pushes `.cursor` changes. Use `--no-push` to skip pushing, and `--no-pr` to skip opening PRs. If a repo already has our install but its `.cursor` has diverged from master, the script opens a PR to the cursor-settings repo proposing those changes before overwriting.

Reconciliation can run automatically on push to main/master: add org names (one per line) to `.github/orgs-to-update.txt`; the Pipeline workflow runs `install-org.sh` for each. Set the repo secret `GH_TOKEN` (a PAT with `repo` and org access) for the workflow to push to org repos and this repo.

**How org reconciliation works:** On push to main/master the Pipeline's reconcile job runs `install-org.sh` which is the single source of truth: it clones org repos with a shallow, sparse checkout (only the `.cursor` directory is checked out; no full history or other files). If a repo’s `.cursor` already matches current cursor-settings, it is skipped. Otherwise the script runs `install.sh` to sync content, then commits and pushes only the `.cursor` tree. If a repo had diverged from master, a PR is opened to this repo to propose those changes before overwriting.

If target repos still have bats in their workflows: run `scripts/remove-bats-from-org-repos.sh <org>` to report which repos have it; use `--fix` to remove the bats job and push. Use `--all-orgs` to scan orgs from `.github/orgs-to-update.txt`.

### Clone a project that already has the submodule

Use `--recurse-submodules` so the submodule is fetched (requires access to both repos):

```bash
git clone --recurse-submodules git@github.com:you/your-project.git
```

If you cloned without `--recurse-submodules`:

```bash
cd your-project
git submodule init
git submodule update
```

### Post changes (from any project)

When you add or edit rules:

```bash
cd your-project/.cursor
git add .
git commit -m "Add rule for X"
git push origin main

cd ..
git add .cursor
git commit -m "Update cursor rules"
git push
```

### Pull changes from other projects

Before pulling, update the submodule to the latest commit:

```bash
cd your-project
git submodule update --remote .cursor
git add .cursor
git commit -m "Update cursor rules"
git push
```

Or manually:

```bash
cd your-project/.cursor
git pull origin main
cd ..
git add .cursor
git commit -m "Update cursor rules"
git push
```

### Multiple projects contributing

Each project has its own clone of the submodule. Any project can push changes; others pull with
`git submodule update --remote .cursor` or `cd .cursor && git pull`.

## Rules Overview

| Rule | Applies | Key Directives |
|------|---------|----------------|
| **code-quality** | Always | CC ≤ 10, params ≤ 7, memory safety, error handling, thread safety |
| **c-misra-safety** | \*.c, \*.h | No recursion, snprintf not sprintf, validate inputs, bounds check |
| **repository-boundaries** | Always | Don't modify build/, .git/; git workflow |
| **file-organization** | Always | .md in docs/ except README.md, AGENTS.md |
| **ci-deploy-verify** | Always | Verify locally first; commit/push/deploy; iterate fix-redeploy until successful |
| **esp-idf-boundaries** | \*.c, \*.h | Component Manager for shared libs, managed_components, idf.py, Kconfig, BLE security |
| **security** | Always | No hardcoded creds, validate inputs |
| **python-flutter-ts** | \*.py, \*.dart, \*.ts, \*.vue | Language-specific |

## Development

### Pre-commit

```bash
pip install -r requirements-dev.txt
pre-commit install
```

Hooks: trailing whitespace, end-of-file fixer, markdownlint, and `.mdc` frontmatter validation.
Run manually: `pre-commit run --all-files`

### CI

GitHub Actions runs on push/PR: validates `.mdc` frontmatter (description, alwaysApply or globs), markdownlint,
and pytest (including tests for `install.sh`). The `tests/` directory is used only in this repo (and in CI). Tests run once in this repo to establish reliability; reconciliation does not run tests in any target repo. The `tests/` directory is not installed into projects’ `.cursor/` (see install script).

### Validate rules manually

```bash
python scripts/validate_mdc.py
python scripts/validate_mdc.py --rules-dir /path/to/rules
RULES_DIR=/path/to/rules python scripts/validate_mdc.py
```

### Install script tests

```bash
python -m pytest tests/test_install.py -v
```

### Adding a new rule

1. Create `rules/name.mdc` with YAML frontmatter:

   ```yaml
   ---
   description: Brief description (required)
   alwaysApply: true   # or globs: "**/*.py"
   ---
   ```

2. Rules need either `alwaysApply: true` or `globs` (non-empty). `globs` may be a string or YAML array.

3. Install dev deps: `pip install -r requirements-dev.txt` (includes pre-commit).

4. Run `pre-commit run --all-files` before committing.

## Source Projects

Compiled from: C2DS-brood, C2DS-bootstrap, C2DS-cognator, C2DS-controller, C2DS-egg, C2DS-clutch,
gecl-app-common, GGG-esp32c3-controller, ELSL-aws-iot-hallway-bathroom-lights,
c2ds_mobile_provisioning_app, C2DS-mother-hen-api-stack, ESPConnect, dot_shellrc.

[Changelog](docs/CHANGELOG.md)
