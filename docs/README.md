# Suigetsukan Lambdas Documentation

## Overview

This repository consolidates all AWS Lambda functions for the Suigetsukan organization.

## Migration

See `scripts/migrate_lambda_repos.sh` for listing, cloning, and migrating individual lambda repos.

## Shared Constants

Magic numbers and strings live in `common/constants.py`. The deploy script copies `common/` into each lambda before packaging. Lambdas import via `from common.constants import ...`.

## Secrets and Environment Variables

See [SECRETS_AND_ENV_VARS.md](SECRETS_AND_ENV_VARS.md) for redacted sensitive data and required GitHub secrets.

## Security Note: cognito-rest-api

The cognito-rest-api Lambda exposes admin actions (approve, promote, deny, delete, list users) without built-in authentication. **Configure API Gateway with a Cognito authorizer** (or Lambda authorizer) so only authenticated admins can invoke it. The Lambda does not validate tokens; that must be done at the API Gateway layer.

## Lambda Triggers

The CI script `setup_triggers.py` configures SQS, EventBridge, and IoT Rule triggers from `config.json` `event_sources`. **SNS is not managed by this repo.**

### file-name-decipher (SNS)

This Lambda is invoked by SNS notifications (e.g. from MediaConvert or another video pipeline). Configure the SNS subscription outside this repo:

1. Create an SNS topic (or use an existing one).
2. Add a subscription: Protocol = Lambda, Endpoint = `suigetsukan-file-name-decipher` ARN.
3. Ensure the Lambda has permission to receive SNS (the role gets `AmazonSNSFullAccess` via deploy_roles).

Event payloads: `Subject` contains "Complete", "Direct", or "Ingest". For "Complete", `Message` is JSON with `hlsUrl`. For "Direct", `Message` is the raw URL.
