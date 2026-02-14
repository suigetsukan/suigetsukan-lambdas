# Changelog

## Unreleased

- validate_mdc: clarify globs error message ("currently empty"); add --rules-dir and RULES_DIR
- validate_mdc: support globs as YAML string or array (Cursor accepts both)
- Add requirements-dev.txt with pre-commit
- Add pyproject.toml (pytest config, project metadata)
- Add bats tests for install.sh (5 tests)
- Add bats CI job
- README: pre-commit run --all-files, requirements-dev, validate_mdc usage, bats tests

## 2026-02-08 (fixes)

- Fix duplicate "Compiled from" block in README
- Use requirements.txt in CI (instead of pip install pyyaml)
- Resolve TARGET_DIR to absolute path in install script
- Add __pycache__/, *.pyc, .pytest_cache/ to .gitignore
- Fix frontmatter regex for empty body in validate_mdc.py
- Pin pyyaml to >=6.0,<7.0
- Add unit tests for validate_mdc.py

## 2026-02-08

### Added

- MIT LICENSE
- `esp-idf-boundaries.mdc` — ESP-IDF-specific content (managed_components, idf.py, Kconfig, BLE)
- pip cache to CI workflow
- Symlink check in install script (rejects `.cursor` when it's a symlink)
- Validation for non-empty `description` and `globs` in validate_mdc.py
- `bin/` to allowed script directories in file-organization rule

### Changed

- Split ESP-IDF content from `repository-boundaries` and `security` into `esp-idf-boundaries`
- Merged `local-first-build` into `ci-deploy-verify`
- Replaced hardcoded `gamename` with `YOUR_ORG` placeholder
- Added project-specific note to `python-flutter-ts` (Mother Hen / ESPConnect defaults)

### Removed

- `local-first-build.mdc` (merged into ci-deploy-verify)
