# suigetsukan-lambdas

Home for all Suigetsukan AWS Lambda functions. Consolidated from individual repos following the [C2DS-lambdas](https://github.com/chicken-coop-door-status/C2DS-lambdas) model.

---

## Overview

This repo contains five Lambdas that power the Suigetsukan curriculum platform: **billing**, **Cognito user management**, **Cognito backup**, and **video-to-curriculum indexing** (file-name-decipher). **DynamoDB backup** is handled by **AWS Backup** in us-west-1 (weekly, 1-year retention); see [scripts/setup_aws_backup_dynamodb.py](scripts/setup_aws_backup_dynamodb.py) and [docs/SECRETS_AND_ENV_VARS.md](docs/SECRETS_AND_ENV_VARS.md). Each Lambda lives under `lambdas/<name>/` with `app.py`, `config.json`, and (where needed) `requirements.txt`. Shared code is in `common/` and is bundled into each Lambda at deploy time.

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

### DynamoDB backup (AWS Backup)

**DynamoDB** is backed up by **AWS Backup**, not a Lambda. **All** DynamoDB tables in **us-west-1** are included (wildcard `table/*`; new tables are included automatically) on a **weekly** schedule with **1-year retention** (automatic prune). Either run the one-time setup script or deploy the CloudFormation template; use profile **tennis@suigetsukan** and ensure DynamoDB is opted in for AWS Backup in that region.

- **Script:** [scripts/setup_aws_backup_dynamodb.py](scripts/setup_aws_backup_dynamodb.py)
- **CloudFormation (IaC):** [infra/aws-backup-dynamodb.yaml](infra/aws-backup-dynamodb.yaml) — `aws cloudformation deploy --template-file infra/aws-backup-dynamodb.yaml --stack-name suigetsukan-ddb-backup --capabilities CAPABILITY_NAMED_IAM --region us-west-1 --profile tennis@suigetsukan`

See [docs/SECRETS_AND_ENV_VARS.md](docs/SECRETS_AND_ENV_VARS.md).

---

### cognito-backup

**Purpose:** Exports **Cognito user pool** users, groups, and pool metadata to S3 on a schedule. No passwords or secrets are stored; restore requires users to reset password or use invite flow.

**Invocation:** EventBridge schedule (**daily** at 3 AM UTC, `cron(0 3 * * ? *)`). Can also be invoked manually.

**Behavior:** Lists all user pools in the region, then for each pool lists all users (with pagination) and each user’s groups, fetches pool metadata, compresses with gzip, and uploads to `s3://{bucket}/backups/YYYY/MM/DD/cognito-users-{pool_id}-{timestamp}.json.gz`. After upload, the backup is **validated** (re-download, decompress, structure and count checks); only then is the manifest at `backups/latest/manifest.json` updated and success returned. Publishes CloudWatch metrics (namespace `CognitoBackup`: TotalUsers, ExecutionDuration). Optional SNS notification on failure. **Retention:** Use **S3 lifecycle** on the backup bucket (prefix `backups/`, expire after 365 days). No pruning script (see [docs/SECRETS_AND_ENV_VARS.md](docs/SECRETS_AND_ENV_VARS.md)).

**Key env:** `AWS_REGION`, `AWS_S3_BACKUP_BUCKET` (required); optional `SNS_SUPPORT_TOPIC_ARN`. The deploy script never sets a single pool ID, so cognito-backup **always** backs up **all** Cognito user pools in the account in the configured region.

| Config        | Value                          |
|---------------|---------------------------------|
| Function name | `suigetsukan-cognito-backup`   |
| Handler       | `app.lambda_handler`           |
| Runtime       | Python 3.12                    |
| Timeout       | 300 s                          |
| Event source  | EventBridge `cron(0 3 * * ? *)` (daily 3 AM UTC) |

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
| cognito-backup           | EventBridge    | Export Cognito users/groups to S3 (daily); validate backup, manifest, metrics |
| file-name-decipher       | SNS            | Map video URLs → curriculum DynamoDB tables |

DynamoDB backup is handled by **AWS Backup** (us-west-1, weekly, 1-year retention); see setup script and docs.

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
