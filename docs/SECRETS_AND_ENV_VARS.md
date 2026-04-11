# Secrets and Environment Variables

This document lists sensitive/proprietary information that was redacted from the code and the GitHub secrets or Lambda environment variables to use instead.

## Summary of Redactions

| Lambda | What Was Redacted | Secret/Env Var |
|--------|-------------------|----------------|
| billing-rest-api | Hardcoded `us-west-1` region for Cost Explorer | `AWS_REGION` |
| cognito-rest-api | Hardcoded `tennis.suigetsukan@gmail.com` (SES sender) | `AWS_SES_SOURCE_EMAIL` |
| cognito-rest-api | Hardcoded `us-west-1` for SES | `AWS_REGION` |
| cognito-post-confirmation | Hardcoded `tennis.suigetsukan@gmail.com` (SES sender) | `AWS_SES_SOURCE_EMAIL` |
| cognito-post-confirmation | Hardcoded `us-west-1` for Cognito/SES | `AWS_REGION` |
| file-name-decipher | Hardcoded DynamoDB table names | `AWS_DDB_AIKIDO_TABLE_NAME`, `AWS_DDB_BATTODO_TABLE_NAME`, `AWS_DDB_DANZAN_RYU_TABLE_NAME` |
| cognito-backup | S3 bucket (required); optional SNS; always backs up all pools (no pool ID at deploy) | `AWS_S3_BACKUP_BUCKET`; `SNS_SUPPORT_TOPIC_ARN` (optional) |

---

## Suggested GitHub Secrets

Add these to your GitHub repository secrets (Settings → Secrets and variables → Actions):

| Secret Name | Description | Used By |
|-------------|-------------|---------|
| `AWS_ACCESS_KEY_ID` | IAM user for deployment | Pipeline (deploy job) |
| `AWS_SECRET_ACCESS_KEY` | IAM user for deployment | Pipeline (deploy job) |
| `AWS_ACCOUNT_ID` | AWS account ID | Pipeline (deploy job) |
| `AWS_REGION` | Primary region (e.g. `us-west-1`) | Pipeline (deploy job), billing-rest-api, cognito lambdas |
| `AWS_SES_SOURCE_EMAIL` | Verified SES sender email | cognito-rest-api, cognito-post-confirmation |
| `AWS_COGNITO_USER_POOL_ID` | Cognito User Pool ID (cognito-rest-api only). Not used by cognito-backup; deploy script omits it so cognito-backup always backs up all user pools in the region. | cognito-rest-api |
| `AWS_DDB_AIKIDO_TABLE_NAME` | DynamoDB table for Aikido curriculum | file-name-decipher |
| `AWS_DDB_BATTODO_TABLE_NAME` | DynamoDB table for Battodo curriculum | file-name-decipher |
| `AWS_DDB_DANZAN_RYU_TABLE_NAME` | DynamoDB table for Danzan Ryu curriculum | file-name-decipher |
| `CORS_ALLOWED_ORIGIN` | Restrict CORS origin (optional; default `*`) | cognito-rest-api, billing-rest-api |
| `AWS_S3_BACKUP_BUCKET` | S3 bucket for Cognito backups (must exist). Retention via **S3 lifecycle only**: add rule for prefix `backups/`, expire after 365 days | cognito-backup |
| `SNS_SUPPORT_TOPIC_ARN` | SNS topic for Cognito backup failure alerts (optional) | cognito-backup |

---

## Per-Lambda Config (config.json env_vars)

Each Lambda's `config.json` defines `env_vars` keys. The deploy script maps these to GitHub secrets of the **same name** (environment variables are expected to be set by the workflow). Placeholder values in config are replaced at deploy time.

### billing-rest-api
- `AWS_REGION` — Cost Explorer region (default `us-west-1`)

### cognito-rest-api
- `AWS_REGION` — Cognito/SES region
- `AWS_COGNITO_USER_POOL_ID` — Cognito User Pool ID
- `AWS_SES_SOURCE_EMAIL` — Verified SES sender

### cognito-post-confirmation
- `AWS_REGION` — Cognito/SES region
- `AWS_SES_SOURCE_EMAIL` — Verified SES sender

### file-name-decipher
- `AWS_DDB_AIKIDO_TABLE_NAME` — e.g. `Aikido-<stack-id>-staging`
- `AWS_DDB_BATTODO_TABLE_NAME` — e.g. `Battodo-<stack-id>-staging`
- `AWS_DDB_DANZAN_RYU_TABLE_NAME` — e.g. `DanzanRyu-<stack-id>-staging`

### DynamoDB backup (AWS Backup, not a Lambda)
DynamoDB is backed up by **AWS Backup** in **us-west-1**: **all** tables in the region (wildcard `table/*`; new tables included automatically), weekly schedule, 1-year retention (automatic prune). Either run the one-time script `scripts/setup_aws_backup_dynamodb.py` with `AWS_PROFILE=tennis@suigetsukan` and `AWS_REGION=us-west-1`, or deploy the CloudFormation template `infra/aws-backup-dynamodb.yaml` (see header comments for the deploy command). Ensure DynamoDB is opted in for AWS Backup in that region (Console: AWS Backup → Settings → Service opt-in). No Lambda or GitHub secrets required for DynamoDB backup.

### cognito-backup
- `AWS_REGION` — Region (e.g. **us-west-1**). Deploy uses profile **tennis@suigetsukan** and region **us-west-1** (or set `AWS_REGION` in CI).
- **All-pools behavior:** The deploy script never sets `AWS_COGNITO_USER_POOL_ID` on the cognito-backup Lambda, so it **always** backs up **all** Cognito user pools in the account in the configured region.
- `AWS_S3_BACKUP_BUCKET` — S3 bucket for exports. The bucket must exist and be writable by the Lambda. **Retention:** Use **S3 lifecycle rules only** (e.g. lifecycle rule on prefix `backups/`, expire after 365 days). No pruning script. Output: gzip-compressed backups at `backups/YYYY/MM/DD/cognito-users-{pool_id}-{timestamp}.json.gz`; manifest at `backups/latest/manifest.json` (`run_timestamp`, `pools` with per-pool `backup_key` and `total_users`). After each upload the backup is validated (re-download, decompress, structure check); CloudWatch metrics (namespace `CognitoBackup`: TotalUsers, ExecutionDuration) are published.
- `SNS_SUPPORT_TOPIC_ARN` (optional) — ARN of SNS topic for error notifications on backup failure.

No passwords are exported; restore requires users to reset password or use invite flow.

---

## Other Lambdas (Not Yet Migrated)

These lambdas use env vars correctly and have no hardcoded secrets:

- **aikido-rest-api**: `AIKIDO_TABLE_NAME`
- **battodo-rest-api**: `BATTODO_TABLE_NAME`
- **danzan-ryu-rest-api**: `DANZAN_RYU_TABLE_NAME`
- **cognito-post-auth**: `AWS_REGION`, `AWS_SES_SOURCE_EMAIL`, `SES_DESTINATION_EMAIL`
- **problem-report-rest-api**: `AWS_SES_SOURCE_EMAIL`, `SES_DESTINATION_EMAIL`, `REGION_NAME`, `S3_BUCKET_NAME`, `S3_FILE_NAME`

When migrating these, retain their existing env var usage.
