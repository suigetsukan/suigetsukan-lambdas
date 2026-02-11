"""
DynamoDB backup Lambda: creates on-demand backups for all tables in the region.
"""

import os
from datetime import datetime

import boto3
from botocore.exceptions import ClientError


def lambda_handler(_event, _context):
    """
    Create on-demand backups for all DynamoDB tables in the configured region.
    Returns a summary of backup results; raises if any backup fails.
    """
    region = os.environ.get("AWS_REGION", "us-west-1")
    client = boto3.client("dynamodb", region_name=region)
    ts = datetime.utcnow().strftime("%Y-%m-%d-%H%M%S")

    tables = client.list_tables()["TableNames"]
    results = []

    for table_name in tables:
        backup_name = f"Suigetsukan-{table_name}-{ts}-Project_Retention_90d"
        try:
            resp = client.create_backup(TableName=table_name, BackupName=backup_name)
            backup_arn = resp["BackupDetails"]["BackupArn"]
            results.append(
                {
                    "table": table_name,
                    "backup_name": backup_name,
                    "backup_arn": backup_arn,
                    "status": "SUCCESS",
                }
            )
            print(f"BACKUP SUCCESS: {table_name} -> {backup_name}")
        except ClientError as e:
            results.append(
                {
                    "table": table_name,
                    "backup_name": backup_name,
                    "status": "FAILED",
                    "error": str(e),
                }
            )
            print(f"BACKUP FAILED: {table_name} - {e}")

    failed = [r for r in results if r["status"] == "FAILED"]
    if failed:
        raise RuntimeError(f"{len(failed)} backup(s) failed: {failed}")

    print(f"Completed {len(results)} backups at {datetime.utcnow().isoformat()}Z")
    return {"backups": results}
