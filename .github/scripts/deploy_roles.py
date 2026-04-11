# File: deploy_roles.py
# Suigetsukan Lambda consolidation

import json
import os
import re
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REPO_ROOT = Path.cwd()
LAMBDAS_DIR = REPO_ROOT / "lambdas"
DEPLOY_ALL = os.getenv("DEPLOY_ALL", "false").lower() == "true"
CHANGED_LAMBDAS_JSON = os.getenv("CHANGED_LAMBDAS", "[]")
ACCOUNT_ID = os.getenv("AWS_ACCOUNT_ID")

iam = boto3.client("iam")


def get_lambda_dirs():
    all_dirs = [p for p in LAMBDAS_DIR.iterdir() if p.is_dir() and (p / "app.py").exists()]
    if DEPLOY_ALL:
        return all_dirs
    try:
        changed = set(json.loads(CHANGED_LAMBDAS_JSON))
    except json.JSONDecodeError:
        changed = set()
    return [d for d in all_dirs if d.name in changed]


def load_config(lambda_dir: Path) -> dict:
    config_path = lambda_dir / "config.json"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return json.load(f)


POLICY_MAP = {
    "ce": "arn:aws:iam::aws:policy/AWSCostExplorerReadOnlyAccess",
    "cloudwatch": "arn:aws:iam::aws:policy/CloudWatchFullAccess",
    "cognito-idp": "arn:aws:iam::aws:policy/AmazonCognitoPowerUser",
    "dynamodb": "arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess",
    "iot": "arn:aws:iam::aws:policy/AWSIoTFullAccess",
    "iot-data": "arn:aws:iam::aws:policy/AWSIoTDataAccess",
    "s3": "arn:aws:iam::aws:policy/AmazonS3FullAccess",
    "ses": "arn:aws:iam::aws:policy/AmazonSESFullAccess",
    "sns": "arn:aws:iam::aws:policy/AmazonSNSFullAccess",
    "sqs": "arn:aws:iam::aws:policy/AmazonSQSFullAccess",
}


def _discover_services(lambda_dir: Path) -> set[str]:
    services: set[str] = set()
    for py_file in lambda_dir.glob("*.py"):
        code = py_file.read_text()
        services.update(re.findall(r'boto3\.client\(["\']([^"\']+)["\']\)', code))
        services.update(re.findall(r'boto3\.resource\(["\']([^"\']+)["\']\)', code))
    return services


def _ensure_policies_attached(role_name: str, services: set[str]) -> None:
    """Attach any policy_map policies for discovered services not already on the role."""
    paginator = iam.get_paginator("list_attached_role_policies")
    attached_arns = set()
    for page in paginator.paginate(RoleName=role_name):
        for p in page.get("AttachedPolicies", []):
            attached_arns.add(p["PolicyArn"])
    for svc in services:
        if svc not in POLICY_MAP:
            continue
        arn = POLICY_MAP[svc]
        if arn not in attached_arns:
            iam.attach_role_policy(RoleName=role_name, PolicyArn=arn)
            print(f"  Attached policy for {svc}: {arn}")
            attached_arns.add(arn)


def _attach_inline_policy_if_present(role_name: str, lambda_dir: Path) -> bool:
    """If lambda_dir/iam_policy.json exists, attach it as inline policy. Return True if attached."""
    policy_path = lambda_dir / "iam_policy.json"
    if not policy_path.exists():
        return False
    with open(policy_path) as f:
        doc = json.load(f)
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="LambdaInlinePolicy",
        PolicyDocument=json.dumps(doc),
    )
    print(f"  Attached inline policy from {policy_path.name}")
    return True


def create_or_update_role(role_name: str, function_name: str, lambda_dir: Path):
    services = _discover_services(lambda_dir)
    try:
        iam.get_role(RoleName=role_name)
        print(f"  Role {role_name} exists")
        _ensure_policies_attached(role_name, services)
        _attach_inline_policy_if_present(role_name, lambda_dir)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            print(f"  Creating role {role_name}")
            trust_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "lambda.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }
            iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description=f"Execution role for {function_name}",
            )
            basic_policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
            iam.attach_role_policy(RoleName=role_name, PolicyArn=basic_policy_arn)
            print(f"  Attached {basic_policy_arn}")
            _ensure_policies_attached(role_name, services)
            _attach_inline_policy_if_present(role_name, lambda_dir)
            time.sleep(30)
        else:
            raise


def main():
    lambda_dirs = get_lambda_dirs()
    if not lambda_dirs:
        print("No Lambdas to process roles for")
        return
    for lambda_dir in sorted(lambda_dirs):
        config = load_config(lambda_dir)
        role_name = config.get("role_name")
        if not role_name:
            continue
        function_name = config.get("function_name") or f"suigetsukan-{lambda_dir.name}"
        create_or_update_role(role_name, function_name, lambda_dir)
    print("Role deployment complete!")


if __name__ == "__main__":
    main()
