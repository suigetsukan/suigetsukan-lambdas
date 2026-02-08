#!/usr/bin/env python3
"""
Deploy Lambdas from lambdas/ directory
- Deploy ALL if DEPLOY_ALL=true
- Reads config.json for env var keys
- Uses GitHub Secrets with the SAME NAME (no Lambda prefix)
- Creates or updates Lambda functions with retry
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REPO_ROOT = Path.cwd()
LAMBDAS_DIR = REPO_ROOT / "lambdas"
COMMON_DIR = REPO_ROOT / "common"
EXECUTION_ROLE = os.getenv("EXECUTION_ROLE")

RESERVED_ENV_VARS = {
    "_HANDLER",
    "_X_AMZN_TRACE_ID",
    "AWS_DEFAULT_REGION",
    "AWS_REGION",
    "AWS_EXECUTION_ENV",
    "AWS_LAMBDA_FUNCTION_NAME",
    "AWS_LAMBDA_FUNCTION_MEMORY_SIZE",
    "AWS_LAMBDA_FUNCTION_VERSION",
    "AWS_LAMBDA_INITIALIZATION_TYPE",
    "AWS_LAMBDA_LOG_GROUP_NAME",
    "AWS_LAMBDA_LOG_STREAM_NAME",
    "AWS_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_LAMBDA_RUNTIME_API",
    "LAMBDA_TASK_ROOT",
    "LAMBDA_RUNTIME_DIR",
    "AWS_LAMBDA_MAX_CONCURRENCY",
}

if not EXECUTION_ROLE:
    print("ERROR: EXECUTION_ROLE environment variable is not set")
    sys.exit(1)

if not LAMBDAS_DIR.exists():
    print(f"ERROR: {LAMBDAS_DIR} directory not found")
    sys.exit(1)

DEPLOY_ALL = os.getenv("DEPLOY_ALL", "false").lower() == "true"
CHANGED_LAMBDAS_JSON = os.getenv("CHANGED_LAMBDAS", "[]")


def run(cmd: str) -> str:
    print(f"> {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Command failed: {result.stderr}")
        sys.exit(1)
    return result.stdout


def load_config(lambda_dir: Path) -> dict:
    config_path = lambda_dir / "config.json"
    if not config_path.exists():
        print("  no config.json – using defaults")
        return {}
    print(f"  Reading {config_path.name}")
    try:
        with open(config_path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"  Invalid config.json: {e}")
        return {}


def build_env_vars(config: dict) -> dict:
    env_vars = {}
    config_env = config.get("env_vars", {})
    if not config_env:
        print("  no env_vars – skipping environment variables")
        return {}
    for key in config_env:
        norm_key = key.upper().replace("-", "_")
        if norm_key in RESERVED_ENV_VARS:
            print(f"  Skipping reserved env var: {norm_key}")
            continue
        value = os.getenv(norm_key, "")
        if not value:
            print(f"  Warning: secret {norm_key} not set – using empty string")
        env_vars[norm_key] = value
    return env_vars


def deploy_lambda(lambda_dir: Path):
    print(f"\n{'=' * 60}\nDeploying: {lambda_dir.name}\n{'=' * 60}")
    original_cwd = Path.cwd()
    zip_path = None
    os.chdir(lambda_dir)
    try:
        config = load_config(lambda_dir)
        layers = config.get("layers", [])
        for i, layer in enumerate(layers):
            layer = layer.replace(
                "${{ secrets.AWS_ACCOUNT_ID }}", os.getenv("AWS_ACCOUNT_ID", "UNKNOWN")
            )
            layer_version = os.getenv("LAYER_VERSION")
            if layer_version:
                layer = layer.replace(":latest", f":{layer_version}")
            layers[i] = layer
        config["layers"] = layers

        function_name = config.get("function_name") or f"suigetsukan-{lambda_dir.name}"
        role_name = config.get("role_name")
        if role_name:
            account_id = os.getenv("AWS_ACCOUNT_ID")
            if not account_id:
                print("ERROR: AWS_ACCOUNT_ID required when role_name is used")
                sys.exit(1)
            role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"
            print(f"  Using per-Lambda role: {role_arn}")
        else:
            role_arn = EXECUTION_ROLE
            print("  Using global EXECUTION_ROLE")

        handler = config.get("handler", "app.lambda_handler")
        runtime = config.get("runtime", "python3.12")
        timeout = config.get("timeout", 30)
        memory = config.get("memory_size", 256)
        layers = config.get("layers", [])
        tags = config.get("tags", {})

        req_file = lambda_dir / "requirements.txt"
        if req_file.exists():
            print("  Installing dependencies with python:3.12-slim...")
            run(
                "docker run --rm -v $PWD:/var/task python:3.12-slim pip install -r /var/task/requirements.txt -t /var/task/"
            )
        else:
            print("  No requirements.txt – skipping dependency install")

        # Copy common/ into lambda dir so it's included in the zip
        common_dest = lambda_dir / "common"
        if COMMON_DIR.exists():
            if common_dest.exists():
                shutil.rmtree(common_dest)
            shutil.copytree(COMMON_DIR, common_dest)
            print("  Copied common/ into lambda dir")

        zip_path = REPO_ROOT / f"{lambda_dir.name}.zip"
        exclude = config.get("exclude_files", ["*.pyc", "__pycache__/*", "tests/**"])
        exclude_args = " ".join(f"-x '{pattern}'" for pattern in exclude)
        print("  Packaging code into zip...")
        run(f"zip -r {zip_path} . {exclude_args} > /dev/null")

        # Remove common/ from lambda dir (cleanup; don't leave it in the repo)
        if common_dest.exists():
            shutil.rmtree(common_dest)

        env_vars = build_env_vars(config)
        region = os.getenv("AWS_REGION", "us-west-1")
        lambda_client = boto3.client("lambda", region_name=region)
        function_arn = (
            f"arn:aws:lambda:{region}:{os.getenv('AWS_ACCOUNT_ID')}:function:{function_name}"
        )

        try:
            print(f"  Updating code for: {function_name}")
            lambda_client.update_function_code(
                FunctionName=function_name, ZipFile=zip_path.read_bytes(), Publish=True
            )
            print("  Code updated. Waiting 5s before config update...")
            time.sleep(5)
            for attempt in range(5):
                try:
                    lambda_client.update_function_configuration(
                        FunctionName=function_name,
                        Environment={"Variables": env_vars},
                        Handler=handler,
                        Runtime=runtime,
                        Timeout=timeout,
                        MemorySize=memory,
                        Role=role_arn,
                        Layers=layers,
                    )
                    print("  Config updated")
                    break
                except ClientError as e:
                    if e.response["Error"]["Code"] == "ResourceConflictException" and attempt < 4:
                        delay = 10 * (attempt + 1)
                        print(f"  Retrying in {delay}s... (attempt {attempt + 1}/5)")
                        time.sleep(delay)
                    else:
                        raise
            if tags:
                lambda_client.tag_resource(Resource=function_arn, Tags=tags)
                print("  Tags updated")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                print(f"  Creating new function: {function_name}")
                lambda_client.create_function(
                    FunctionName=function_name,
                    Runtime=runtime,
                    Role=role_arn,
                    Handler=handler,
                    Code={"ZipFile": zip_path.read_bytes()},
                    Timeout=timeout,
                    MemorySize=memory,
                    Layers=layers,
                    Publish=True,
                    Environment={"Variables": env_vars} if env_vars else {},
                    Tags=tags,
                )
            else:
                raise
        print(f"  Done: {function_name}")
    finally:
        os.chdir(original_cwd)
        if zip_path is not None and zip_path.exists():
            zip_path.unlink()


def main():
    all_dirs = [p for p in LAMBDAS_DIR.iterdir() if p.is_dir() and (p / "app.py").exists()]
    if not all_dirs:
        print("No valid Lambda directories found (must contain app.py)")
        return
    if DEPLOY_ALL:
        print("DEPLOY_ALL=true → deploying ALL Lambdas")
        lambda_dirs = all_dirs
    else:
        try:
            changed = set(json.loads(CHANGED_LAMBDAS_JSON))
        except json.JSONDecodeError:
            print("Invalid CHANGED_LAMBDAS_JSON → deploying all")
            changed = set()
        lambda_dirs = [d for d in all_dirs if d.name in changed]
    if not lambda_dirs:
        print("No Lambdas to deploy.")
        return
    print(f"Found {len(lambda_dirs)} Lambda(s) to deploy:")
    for d in lambda_dirs:
        print(f"  - {d.name}")
    for lambda_dir in sorted(lambda_dirs):
        deploy_lambda(lambda_dir)
    print("\nDeployment complete!")


if __name__ == "__main__":
    main()
