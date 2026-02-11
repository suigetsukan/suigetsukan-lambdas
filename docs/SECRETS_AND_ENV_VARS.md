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
| ddb-backup | Region for DynamoDB (us-west-1) | `AWS_REGION` |

---

## Suggested GitHub Secrets

Add these to your GitHub repository secrets (Settings → Secrets and variables → Actions):

| Secret Name | Description | Used By |
|-------------|-------------|---------|
| `AWS_ACCESS_KEY_ID` | IAM user for deployment | Deploy workflow |
| `AWS_SECRET_ACCESS_KEY` | IAM user for deployment | Deploy workflow |
| `AWS_ACCOUNT_ID` | AWS account ID | Deploy workflow |
| `AWS_REGION` | Primary region (e.g. `us-west-1`) | Deploy workflow, billing-rest-api, cognito lambdas |
| `AWS_SES_SOURCE_EMAIL` | Verified SES sender email | cognito-rest-api, cognito-post-confirmation |
| `AWS_COGNITO_USER_POOL_ID` | Cognito User Pool ID | cognito-rest-api |
| `AWS_DDB_AIKIDO_TABLE_NAME` | DynamoDB table for Aikido curriculum | file-name-decipher |
| `AWS_DDB_BATTODO_TABLE_NAME` | DynamoDB table for Battodo curriculum | file-name-decipher |
| `AWS_DDB_DANZAN_RYU_TABLE_NAME` | DynamoDB table for Danzan Ryu curriculum | file-name-decipher |
| `CORS_ALLOWED_ORIGIN` | Restrict CORS origin (optional; default `*`) | cognito-rest-api, billing-rest-api |

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

### ddb-backup
- `AWS_REGION` — Region for DynamoDB tables (e.g. `us-west-1`). Backs up all tables in this region with retention naming `Suigetsukan-{TABLE_NAME}-{ts}-Project_Retention_90d`. Runs daily via EventBridge schedule.

---

## Other Lambdas (Not Yet Migrated)

These lambdas use env vars correctly and have no hardcoded secrets:

- **aikido-rest-api**: `AIKIDO_TABLE_NAME`
- **battodo-rest-api**: `BATTODO_TABLE_NAME`
- **danzan-ryu-rest-api**: `DANZAN_RYU_TABLE_NAME`
- **cognito-post-auth**: `AWS_REGION`, `AWS_SES_SOURCE_EMAIL`, `SES_DESTINATION_EMAIL`
- **problem-report-rest-api**: `AWS_SES_SOURCE_EMAIL`, `SES_DESTINATION_EMAIL`, `REGION_NAME`, `S3_BUCKET_NAME`, `S3_FILE_NAME`

When migrating these, retain their existing env var usage.
