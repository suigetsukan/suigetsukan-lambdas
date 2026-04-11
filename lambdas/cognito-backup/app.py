"""
Cognito User Pool Backup Lambda Function

Performs a backup of Amazon Cognito User Pool(s) users, groups, and metadata.
- When AWS_COGNITO_USER_POOL_ID is set: backs up that single pool (same key/manifest shape as before).
- When AWS_COGNITO_USER_POOL_ID is unset: lists all user pools in the account/region and backs up each.

Exports user data including attributes (with PII), status, groups, and MFA options (limited),
along with pool-level metadata. Backup is JSON, gzip, uploaded to S3 in date-partitioned keys.
After upload, each backup is validated (re-download, decompress, parse, structure and count checks);
then the manifest is updated and success returned.
Retention is via S3 lifecycle rules only (e.g. expire prefix backups/ after 365 days).

Environment Variables:
- AWS_REGION (optional): Region for Cognito/S3/SNS/CloudWatch (default us-west-1).
- AWS_COGNITO_USER_POOL_ID (optional): Single pool ID to backup; when unset, all pools in region are backed up.
- AWS_S3_BACKUP_BUCKET (required): The S3 bucket name for storing backups.
- SNS_SUPPORT_TOPIC_ARN (optional): ARN of the SNS topic for error notifications.
"""

import base64
import gzip
import hashlib
import io
import json
import os
import time
from datetime import datetime, UTC

import boto3
from botocore.exceptions import ClientError

_REGION = os.environ.get("AWS_REGION", "us-west-1")
PREFIX = "backups/"


def _get_clients():
    return {
        "cognito": boto3.client("cognito-idp", region_name=_REGION),
        "s3": boto3.client("s3", region_name=_REGION),
        "sns": boto3.client("sns", region_name=_REGION),
        "cloudwatch": boto3.client("cloudwatch", region_name=_REGION),
    }


def _content_md5(body):
    """Return base64-encoded MD5 digest of body for S3 ContentMD5 (integrity only, not crypto)."""
    return base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode("ascii")


def _verify_s3_object_exists(s3_client, bucket, key):
    """Verify object exists in S3 and has non-zero size; raise if not."""
    head = s3_client.head_object(Bucket=bucket, Key=key)
    size = head.get("ContentLength", 0)
    if size is None or size <= 0:
        raise RuntimeError(f"S3 object s3://{bucket}/{key} has invalid size: {size}")


def validate_backup_in_s3(s3_client, bucket, key):
    """
    Re-download backup from S3, decompress, parse JSON; assert required structure and count.
    Raises ValueError on any failure so the handler can notify SNS and fail the run.
    """
    resp = s3_client.get_object(Bucket=bucket, Key=key)
    body = resp["Body"].read()
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(body), mode="rb") as gz:
            raw = gz.read().decode("utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"Backup decompress or JSON parse failed: {e}") from e

    required = (
        "timestamp",
        "COGNITO_USER_POOL_ID",
        "total_users",
        "users",
        "groups",
        "pool_metadata",
    )
    for field in required:
        if field not in data:
            raise ValueError(f"Backup missing required field: {field}")

    if not isinstance(data["users"], list):
        raise ValueError(f"Backup 'users' must be a list, got {type(data['users']).__name__}")
    if not isinstance(data["pool_metadata"], dict):
        raise ValueError(
            f"Backup 'pool_metadata' must be a dict, got {type(data['pool_metadata']).__name__}"
        )
    if len(data["users"]) != data["total_users"]:
        raise ValueError(
            f"Backup user count mismatch: len(users)={len(data['users'])}, "
            f"total_users={data['total_users']}"
        )


def _get_all_users(cognito_client, user_pool_id):
    users = []
    pagination_token = None
    while True:
        kwargs = {"UserPoolId": user_pool_id, "Limit": 60}
        if pagination_token:
            kwargs["PaginationToken"] = pagination_token
        response = cognito_client.list_users(**kwargs)
        fetched = response.get("Users", [])
        users.extend(fetched)
        pagination_token = response.get("PaginationToken")
        if not pagination_token:
            break
    return users


def _get_user_groups(cognito_client, user_pool_id, username):
    groups = []
    pagination_token = None
    while True:
        kwargs = {"UserPoolId": user_pool_id, "Username": username, "Limit": 60}
        if pagination_token:
            kwargs["NextToken"] = pagination_token
        response = cognito_client.admin_list_groups_for_user(**kwargs)
        fetched = [g["GroupName"] for g in response.get("Groups", [])]
        groups.extend(fetched)
        pagination_token = response.get("NextToken")
        if not pagination_token:
            break
    return groups


def _list_all_user_pool_ids(cognito_client):
    """Return all user pool IDs in the account/region (paginated)."""
    pool_ids = []
    next_token = None
    while True:
        kwargs = {"MaxResults": 60}
        if next_token:
            kwargs["NextToken"] = next_token
        response = cognito_client.list_user_pools(**kwargs)
        for pool in response.get("UserPools", []):
            pool_ids.append(pool["Id"])
        next_token = response.get("NextToken")
        if not next_token:
            break
    return pool_ids


def _backup_one_pool(cognito, s3, bucket_name, user_pool_id, date_path, timestamp, key_suffix):
    """
    Backup a single user pool: fetch users/groups/metadata, gzip, upload, validate.
    key_suffix is used in the object key, e.g. timestamp only (single-pool) or pool_id-timestamp (all-pools).
    Returns dict with backup_key and total_users.
    """
    key = f"{date_path}/cognito-users-{key_suffix}.json.gz"

    all_users = _get_all_users(cognito, user_pool_id)
    users_data = []
    for user in all_users:
        username = user["Username"]
        groups = _get_user_groups(cognito, user_pool_id, username)
        user_record = {
            "Username": username,
            "Attributes": {a["Name"]: a["Value"] for a in user.get("Attributes", [])},
            "Status": user.get("UserStatus"),
            "Enabled": user.get("Enabled"),
            "UserCreateDate": (
                user["UserCreateDate"].isoformat() if user.get("UserCreateDate") else None
            ),
            "UserLastModifiedDate": (
                user["UserLastModifiedDate"].isoformat()
                if user.get("UserLastModifiedDate")
                else None
            ),
            "Groups": groups,
            "Mfa": user.get("MFAOptions", []),
        }
        users_data.append(user_record)

    users_data.sort(key=lambda x: x["Username"])

    groups_response = cognito.list_groups(UserPoolId=user_pool_id, Limit=50)
    all_groups = [g["GroupName"] for g in groups_response.get("Groups", [])]

    pool_info = cognito.describe_user_pool(UserPoolId=user_pool_id)["UserPool"]
    pool_metadata = {
        "Name": pool_info.get("Name"),
        "CreationDate": (
            pool_info["CreationDate"].isoformat() if "CreationDate" in pool_info else None
        ),
        "LastModifiedDate": (
            pool_info["LastModifiedDate"].isoformat() if "LastModifiedDate" in pool_info else None
        ),
        "MfaConfiguration": pool_info.get("MfaConfiguration"),
        "AccountRecoverySetting": pool_info.get("AccountRecoverySetting"),
    }

    backup_content = {
        "timestamp": timestamp,
        "COGNITO_USER_POOL_ID": user_pool_id,
        "total_users": len(users_data),
        "users": users_data,
        "groups": all_groups,
        "pool_metadata": pool_metadata,
    }

    compressed_buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed_buffer, mode="wb") as gz:
        gz.write(json.dumps(backup_content, indent=2, default=str).encode("utf-8"))
    compressed_buffer.seek(0)
    backup_bytes = compressed_buffer.getvalue()

    s3.put_object(
        Bucket=bucket_name,
        Key=key,
        Body=backup_bytes,
        ContentType="application/gzip",
        ContentEncoding="gzip",
        ContentMD5=_content_md5(backup_bytes),
    )
    _verify_s3_object_exists(s3, bucket_name, key)
    validate_backup_in_s3(s3, bucket_name, key)

    return {"backup_key": key, "total_users": len(users_data)}


def lambda_handler(event, context):
    start_time = time.time()
    user_pool_id = (os.environ.get("AWS_COGNITO_USER_POOL_ID") or "").strip()
    bucket_name = (os.environ.get("AWS_S3_BACKUP_BUCKET") or "").strip()
    sns_topic_arn = os.environ.get("SNS_SUPPORT_TOPIC_ARN")

    if not bucket_name:
        raise ValueError("AWS_S3_BACKUP_BUCKET must be set")

    clients = _get_clients()
    cognito = clients["cognito"]
    s3 = clients["s3"]
    sns_client = clients["sns"]
    cloudwatch = clients["cloudwatch"]

    now = datetime.now(UTC)
    year = now.strftime("%Y")
    month = now.strftime("%m")
    day = now.strftime("%d")
    timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    date_path = f"{PREFIX}{year}/{month}/{day}"

    manifest_key = f"{PREFIX}latest/manifest.json"

    try:
        print("Lambda execution started.")
        print(f"Bucket name: {bucket_name}")

        if user_pool_id:
            # Single-pool mode: one backup, current manifest shape
            print(f"Single-pool mode: {user_pool_id}")
            result = _backup_one_pool(
                cognito, s3, bucket_name, user_pool_id, date_path, timestamp, timestamp
            )
            key = result["backup_key"]
            total_users = result["total_users"]
            print(f"Compressed backup saved and verified at s3://{bucket_name}/{key}")
            print("Backup validation passed.")

            manifest = {
                "latest_timestamp": timestamp,
                "total_users": total_users,
                "backup_key": key,
            }
            manifest_body = json.dumps(manifest, indent=2).encode("utf-8")
            s3.put_object(
                Bucket=bucket_name,
                Key=manifest_key,
                Body=manifest_body,
                ContentMD5=_content_md5(manifest_body),
            )
            _verify_s3_object_exists(s3, bucket_name, manifest_key)
            print(f"Manifest updated and verified at s3://{bucket_name}/{manifest_key}")

            execution_duration = time.time() - start_time
            cloudwatch.put_metric_data(
                Namespace="CognitoBackup",
                MetricData=[
                    {"MetricName": "TotalUsers", "Value": total_users, "Unit": "Count"},
                    {
                        "MetricName": "ExecutionDuration",
                        "Value": execution_duration,
                        "Unit": "Seconds",
                    },
                ],
            )
            print("Metrics published.")
            print("Lambda execution completed successfully.")
            return {"status": "success", "backup_key": key}

        # All-pools mode: list pools, backup each, write multi-pool manifest
        pool_ids = _list_all_user_pool_ids(cognito)
        print(f"All-pools mode: found {len(pool_ids)} pool(s) in region.")

        pools_manifest = {}
        errors = []
        total_users_sum = 0

        for pid in pool_ids:
            try:
                print(f"Backing up pool: {pid}")
                result = _backup_one_pool(
                    cognito, s3, bucket_name, pid, date_path, timestamp, f"{pid}-{timestamp}"
                )
                pools_manifest[pid] = {
                    "backup_key": result["backup_key"],
                    "total_users": result["total_users"],
                }
                total_users_sum += result["total_users"]
                print(f"  Backup saved at s3://{bucket_name}/{result['backup_key']}")
            except (ValueError, RuntimeError, OSError, ClientError) as e:
                err_msg = f"{pid}: {str(e)}"
                print(f"  Failed: {err_msg}")
                errors.append(err_msg)

        if errors:
            error_msg = "Cognito Backup failed for one or more pools:\n" + "\n".join(errors)
            print(error_msg)
            if sns_topic_arn:
                sns_client.publish(
                    TopicArn=sns_topic_arn,
                    Subject="Cognito Backup Failure",
                    Message=error_msg,
                )
            raise RuntimeError(error_msg)

        manifest = {
            "run_timestamp": timestamp,
            "pools": pools_manifest,
        }
        manifest_body = json.dumps(manifest, indent=2).encode("utf-8")
        s3.put_object(
            Bucket=bucket_name,
            Key=manifest_key,
            Body=manifest_body,
            ContentMD5=_content_md5(manifest_body),
        )
        _verify_s3_object_exists(s3, bucket_name, manifest_key)
        print(f"Manifest updated and verified at s3://{bucket_name}/{manifest_key}")

        execution_duration = time.time() - start_time
        cloudwatch.put_metric_data(
            Namespace="CognitoBackup",
            MetricData=[
                {"MetricName": "TotalUsers", "Value": total_users_sum, "Unit": "Count"},
                {"MetricName": "ExecutionDuration", "Value": execution_duration, "Unit": "Seconds"},
            ],
        )
        print("Metrics published.")
        print("Lambda execution completed successfully.")
        return {
            "status": "success",
            "pools": list(pools_manifest.keys()),
            "backup_keys": [p["backup_key"] for p in pools_manifest.values()],
        }

    except Exception as e:
        if user_pool_id:
            error_msg = f"Cognito Backup failed for User Pool {user_pool_id}: {str(e)}"
        else:
            error_msg = str(e)
        print(error_msg)
        if sns_topic_arn:
            sns_client.publish(
                TopicArn=sns_topic_arn,
                Subject="Cognito Backup Failure",
                Message=error_msg,
            )
        raise
