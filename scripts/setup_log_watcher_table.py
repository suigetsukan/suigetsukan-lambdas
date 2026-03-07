#!/usr/bin/env python3
"""
Create DynamoDB table for log-watcher dedupe and throttle state.

Run once per account/region before deploying the log-watcher Lambda.
Uses PAY_PER_REQUEST billing and TTL for automatic cleanup.
"""
from __future__ import annotations

import argparse
import sys

import boto3
from botocore.exceptions import ClientError

DEFAULT_TABLE = "suigetsukan-log-watcher-dedup"
TTL_ATTRIBUTE = "expires_at"


def ensure_table(dynamodb, table_name: str) -> None:
    """Create table if missing; enable TTL."""
    try:
        dynamodb.describe_table(TableName=table_name)
        print(f"Table exists: {table_name}")
        return
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    print(f"Creating table: {table_name}")
    dynamodb.create_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    waiter = dynamodb.get_waiter("table_exists")
    waiter.wait(TableName=table_name)

    print(f"Enabling TTL on {table_name} ({TTL_ATTRIBUTE})")
    dynamodb.update_time_to_live(
        TableName=table_name,
        TimeToLiveSpecification={
            "Enabled": True,
            "AttributeName": TTL_ATTRIBUTE,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create log-watcher DynamoDB dedupe/throttle table."
    )
    parser.add_argument(
        "--region",
        default="us-east-2",
        help="AWS region (default: us-east-2).",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="AWS CLI profile (optional).",
    )
    parser.add_argument(
        "--table",
        default=DEFAULT_TABLE,
        help=f"Table name (default: {DEFAULT_TABLE}).",
    )
    args = parser.parse_args()

    session = boto3.Session(
        profile_name=args.profile,
        region_name=args.region,
    )
    dynamodb = session.client("dynamodb")
    ensure_table(dynamodb, args.table)
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except ClientError as exc:
        print(f"AWS error: {exc}", file=sys.stderr)
        sys.exit(1)
