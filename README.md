# suigetsukan-lambdas

Home for all Suigetsukan AWS Lambda functions. Consolidated from individual repos following the [C2DS-lambdas](https://github.com/chicken-coop-door-status/C2DS-lambdas) model.

## Lambdas

| Name | Description |
|------|-------------|
| billing-rest-api | REST wrapper for AWS Cost Explorer (billing) |
| cognito-post-confirmation | Cognito post-confirmation trigger; adds new users to unapproved group, emails admins |
| cognito-rest-api | REST API for Cognito user admin (approve, promote, deny, close, delete) |
| file-name-decipher | Maps SNS video filenames to DynamoDB curriculum tables (aikido, battodo, danzan_ryu) |

## Migration

```bash
./scripts/migrate_lambda_repos.sh list      # List lambda repos
./scripts/migrate_lambda_repos.sh clone     # Clone all into /tmp
./scripts/migrate_lambda_repos.sh migrate <repo-name>  # Migrate one into lambdas/
```

## Coding Standards (MISRA-Inspired)

See [docs/CODING_GUIDELINES.md](docs/CODING_GUIDELINES.md). Enforcement via ruff, mypy, bandit, and pytest.

### Pre-commit Hook

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt boto3
pre-commit install
```

The hook runs on `git commit`. To run manually: `pre-commit run --all-files`.

### Local Checks

```bash
ruff check . --fix && ruff format .
mypy .
bandit -r lambdas common .github/scripts -ll -c pyproject.toml
pytest tests/ -v
```

## Testing

Tests run on push and pull request via GitHub Actions. **Deploy only proceeds if all lint, type, security, and test checks pass.**

## Deployment

- Each Lambda has `lambdas/<name>/app.py`, `config.json`, and `requirements.txt`
- Push to trigger CI; only changed Lambdas are deployed
- Add `deploy_all` to commit message to force full deploy
