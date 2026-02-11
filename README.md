# suigetsukan-lambdas

Home for all Suigetsukan AWS Lambda functions. Consolidated from individual repos following the [C2DS-lambdas](https://github.com/chicken-coop-door-status/C2DS-lambdas) model.

---

## Overview

This repo contains five Lambdas that power the Suigetsukan curriculum platform: **billing**, **Cognito user management**, **DynamoDB backup**, and **video-to-curriculum indexing** (file-name-decipher). Each Lambda lives under `lambdas/<name>/` with `app.py`, `config.json`, and (where needed) `requirements.txt`. Shared code is in `common/` and is bundled into each Lambda at deploy time.

---

## Lambdas

### billing-rest-api

**Purpose:** REST API that exposes AWS Cost Explorer data (billing and forecast) for the Suigetsukan curriculum project.

**Invocation:** API Gateway (GET). Configure a Cognito (or Lambda) authorizer so only authenticated users can call it.

**Behavior:** Returns JSON with `this_month`, `last_month`, and `forecast` costs. Uses AWS Cost Explorer (`ce`) in the configured region.

**Key env:** `AWS_REGION`, optional `CORS_ALLOWED_ORIGIN`.

| Config        | Value        |
|---------------|--------------|
| Function name | `suigetsukan-billing-rest-api` |
| Handler       | `app.lambda_handler`            |
| Runtime       | Python 3.12                    |
| Timeout       | 300 s                          |

---

### cognito-post-confirmation

**Purpose:** Cognito **Post Confirmation** trigger. Runs after a new user confirms sign-up; places them in the “unapproved” group and notifies administrators by email.

**Invocation:** Automatically by Cognito when a user completes confirmation (e.g. email link).

**Behavior:** Adds the user to the `UNAPPROVED` group, compiles admin emails from the `ADMIN` group, and sends a single “New user” email via SES. Other trigger sources are logged and skipped.

**Key env:** `AWS_REGION`, `AWS_SES_SOURCE_EMAIL`.

| Config        | Value                             |
|---------------|-----------------------------------|
| Function name | `suigetsukan-cognito-post-confirmation` |
| Handler       | `app.handler`                     |
| Runtime       | Python 3.12                      |
| Timeout       | 300 s                             |

---

### cognito-rest-api

**Purpose:** REST API for **Cognito user administration**: list users, approve, promote, deny, close, or delete accounts. Used by an admin UI.

**Invocation:** API Gateway (GET/POST/OPTIONS). **Must be protected with a Cognito authorizer** (or equivalent) so only admins can call it; the Lambda does not validate tokens.

**Behavior:**
- **GET** `/list` — list all users with unapproved/approved breakdown; `/list/admin` — list admin users.
- **POST** `/approve`, `/promote`, `/deny`, `/close`, `/delete` — body: `user`, `user_email`, `admin_email`; moves users between groups or deletes and sends SES notifications as configured.

**Key env:** `AWS_REGION`, `AWS_COGNITO_USER_POOL_ID`, `AWS_SES_SOURCE_EMAIL`, optional `CORS_ALLOWED_ORIGIN`.

| Config        | Value                         |
|---------------|-------------------------------|
| Function name | `suigetsukan-cognito-rest-api` |
| Handler       | `app.handler`                 |
| Runtime       | Python 3.12                   |
| Timeout       | 300 s                         |

---

### ddb-backup

**Purpose:** Creates **on-demand DynamoDB backups** for all tables in the Lambda’s region. Backups are named with a timestamp and a 90-day retention label (e.g. `Suigetsukan-<TableName>-<timestamp>-Project_Retention_90d`).

**Invocation:** EventBridge schedule (default: **every 7 days**). Can also be invoked manually.

**Behavior:** Lists all DynamoDB tables in `AWS_REGION`, calls `create_backup` for each, and returns a summary. Raises if any single backup fails.

**Key env:** `AWS_REGION`.

| Config        | Value                    |
|---------------|--------------------------|
| Function name | `suigetsukan-ddb-backup` |
| Handler       | `app.lambda_handler`     |
| Runtime       | Python 3.12             |
| Timeout       | 300 s                    |
| Event source  | EventBridge `rate(7 days)` |

---

### file-name-decipher

**Purpose:** Maps **video filenames (HLS URLs)** from SNS notifications to the correct curriculum DynamoDB table and record, then writes the HLS URL into the appropriate “variations” field. Supports **Aikido**, **Battodo**, and **Danzan Ryu** arts.

**Invocation:** SNS (e.g. from MediaConvert or another video pipeline). SNS subscription must be configured outside this repo (Protocol = Lambda, Endpoint = this Lambda’s ARN).

**Behavior:**
- **Subject:** `"Complete"` — `Message` is JSON with `hlsUrl`; `"Direct"` — `Message` is the raw URL string; `"Ingest"` — no URL, no DB update.
- Extracts the file stem from the URL (e.g. `a0101x` from `.../a0101x.m3u8`). First character routes to art: `a` → Aikido, `d` → Danzan Ryu, `b`–`m` (excluding `a`/`d`) → Battodo. Then updates the corresponding DynamoDB item’s variations with the new HLS URL.

**Key env:** `AWS_DDB_AIKIDO_TABLE_NAME`, `AWS_DDB_BATTODO_TABLE_NAME`, `AWS_DDB_DANZAN_RYU_TABLE_NAME`.

| Config        | Value                           |
|---------------|----------------------------------|
| Function name | `suigetsukan-file-name-decipher` |
| Handler       | `app.lambda_handler`            |
| Runtime       | Python 3.12                     |
| Timeout       | 300 s                           |

File naming rules and patterns are documented in [docs/FILE_NAMING_CONVENTIONS.md](docs/FILE_NAMING_CONVENTIONS.md).

---

## Quick reference

| Lambda                   | Trigger        | Main role                          |
|--------------------------|----------------|------------------------------------|
| billing-rest-api         | API Gateway    | Cost Explorer billing/forecast API |
| cognito-post-confirmation| Cognito        | Post-confirmation: unapproved + email admins |
| cognito-rest-api         | API Gateway    | Admin: approve, promote, deny, delete users |
| ddb-backup               | EventBridge    | On-demand DynamoDB backups (e.g. weekly) |
| file-name-decipher       | SNS            | Map video URLs → curriculum DynamoDB tables |

---

## Migration

Scripts to list, clone, and migrate legacy lambda repos:

```bash
./scripts/migrate_lambda_repos.sh list                    # List lambda repos
./scripts/migrate_lambda_repos.sh clone                   # Clone all into /tmp
./scripts/migrate_lambda_repos.sh migrate <repo-name>     # Migrate one into lambdas/
```

---

## Development

### Coding standards (MISRA-inspired)

See [docs/CODING_GUIDELINES.md](docs/CODING_GUIDELINES.md). Enforcement via ruff, mypy, bandit, and pytest.

### Quality checks (local and CI)

Run the same checks as CI (pylint, pytest, mypy, ruff, bandit, safety, config validation, complexity):

```bash
./scripts/check_quality.sh
```

### Pre-commit hook

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt boto3
pre-commit install
```

The hook runs on `git commit`. To run manually: `pre-commit run --all-files`.

### Manual lint/test commands

```bash
ruff check . --fix && ruff format .
mypy .
bandit -r lambdas common .github/scripts -ll -c pyproject.toml
pytest tests/ -v
```

---

## Testing

Tests run on push and pull request via GitHub Actions. **Deploy only proceeds if all lint, type, security, and test checks pass.**

---

## Deployment

- Each Lambda has `lambdas/<name>/app.py`, `config.json`, and (where applicable) `requirements.txt`.
- Push to trigger CI; only changed Lambdas are deployed.
- Add `deploy_all` to the commit message to force a full deploy.

---

## Documentation

- [docs/README.md](docs/README.md) — shared constants, triggers, SNS setup, security notes.
- [docs/CODING_GUIDELINES.md](docs/CODING_GUIDELINES.md) — style and tooling.
- [docs/FILE_NAMING_CONVENTIONS.md](docs/FILE_NAMING_CONVENTIONS.md) — file-name-decipher input naming by art.
- [docs/SECRETS_AND_ENV_VARS.md](docs/SECRETS_AND_ENV_VARS.md) — secrets and environment variables.
