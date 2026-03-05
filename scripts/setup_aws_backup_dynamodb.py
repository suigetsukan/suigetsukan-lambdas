#!/usr/bin/env python3
"""
One-time setup for AWS Backup: all DynamoDB tables in the region, weekly schedule, 1-year retention.

Backs up ALL DynamoDB tables (wildcard arn:...:table/*). New tables are included automatically.

Run with: AWS_PROFILE=tennis@suigetsukan AWS_REGION=us-west-1 python scripts/setup_aws_backup_dynamodb.py

Alternative: deploy infra/aws-backup-dynamodb.yaml with CloudFormation (same behavior, IaC).
Ensure DynamoDB is opted in for AWS Backup in the region (Console: AWS Backup → Settings → Service opt-in).
"""

import os
import sys
from typing import cast

import boto3
from botocore.exceptions import ClientError

REGION = os.environ.get("AWS_REGION", "us-west-1")
PLAN_NAME = "Suigetsukan-DynamoDB-Weekly"
VAULT_NAME = "Default"
SELECTION_NAME = "Suigetsukan-DynamoDB-Tables"
BACKUP_ROLE_NAME = "AWSBackupDefaultServiceRole"
# Weekly: Monday 05:00 UTC
SCHEDULE_CRON = "cron(0 5 ? * 1 *)"
RETENTION_DAYS = 365


def get_account_id(session: boto3.Session) -> str:
    sts = session.client("sts", region_name=REGION)
    return cast(str, sts.get_caller_identity()["Account"])


def get_backup_role_arn(session: boto3.Session, account_id: str) -> str:
    iam = session.client("iam", region_name=REGION)
    try:
        iam.get_role(RoleName=BACKUP_ROLE_NAME)
        return f"arn:aws:iam::{account_id}:role/{BACKUP_ROLE_NAME}"
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            print(
                f"ERROR: IAM role {BACKUP_ROLE_NAME} not found. "
                "Use AWS Backup in the Console once in this region to create it, or create it manually.",
                file=sys.stderr,
            )
        raise


def find_plan_by_name(backup_client) -> str | None:
    paginator = backup_client.get_paginator("list_backup_plans")
    for page in paginator.paginate():
        for plan in page.get("BackupPlansList", []):
            plan_id = plan["BackupPlanId"]
            try:
                detail = backup_client.get_backup_plan(BackupPlanId=plan_id)
                if detail.get("BackupPlan", {}).get("BackupPlanName") == PLAN_NAME:
                    return cast(str, plan_id)
            except ClientError:
                continue
    return None


def create_backup_plan(backup_client) -> str:
    plan = {
        "BackupPlanName": PLAN_NAME,
        "Rules": [
            {
                "RuleName": "WeeklyDynamoDB",
                "TargetBackupVaultName": VAULT_NAME,
                "ScheduleExpression": SCHEDULE_CRON,
                "Lifecycle": {"DeleteAfterDays": RETENTION_DAYS},
            }
        ],
    }
    resp = backup_client.create_backup_plan(BackupPlan=plan)
    return cast(str, resp["BackupPlanId"])


def selection_exists(backup_client, plan_id: str) -> bool:
    paginator = backup_client.get_paginator("list_backup_selections")
    for page in paginator.paginate(BackupPlanId=plan_id):
        for sel in page.get("BackupSelectionsList", []):
            if sel.get("SelectionName") == SELECTION_NAME:
                return True
    return False


def create_backup_selection(
    backup_client, plan_id: str, role_arn: str, account_id: str
) -> None:
    # Wildcard: all DynamoDB tables in this account/region (new tables included automatically)
    resources = [f"arn:aws:dynamodb:{REGION}:{account_id}:table/*"]
    selection = {
        "SelectionName": SELECTION_NAME,
        "IamRoleArn": role_arn,
        "Resources": resources,
    }
    backup_client.create_backup_selection(
        BackupPlanId=plan_id, BackupSelection=selection
    )


def main() -> None:
    session = boto3.Session(region_name=REGION)
    account_id = get_account_id(session)
    role_arn = get_backup_role_arn(session, account_id)
    backup = session.client("backup", region_name=REGION)

    plan_id = find_plan_by_name(backup)
    if not plan_id:
        plan_id = create_backup_plan(backup)
        print(f"Created backup plan: {PLAN_NAME} ({plan_id})")
    else:
        print(f"Using existing backup plan: {PLAN_NAME} ({plan_id})")

    if selection_exists(backup, plan_id):
        print(f"Backup selection '{SELECTION_NAME}' already exists; skipping.")
    else:
        create_backup_selection(backup, plan_id, role_arn, account_id)
        print(f"Created backup selection: {SELECTION_NAME} (all DynamoDB tables in {REGION})")

    print("Done. Ensure DynamoDB is opted in for AWS Backup in this region (Console: AWS Backup → Settings).")


if __name__ == "__main__":
    main()
