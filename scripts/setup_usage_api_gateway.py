#!/usr/bin/env python3
"""
Idempotent setup for the UsageAPI REST endpoint.

Creates (or updates) a REST API named ``UsageAPI`` that proxies to the
``suigetsukan-usage-rest-api`` Lambda, matching the same shape as the
existing BillingAPI / CognitoAPI:
  - resource:        /usage
  - proxy resource:  /usage/{proxy+}      (ANY method, AWS_PROXY integration)
  - OPTIONS         MOCK integration returning CORS headers
  - stage:           prod1

After running, prints the invoke URL to plug into
``suigetsukan-curriculum`` ``src/config.js``.

Usage:
  python scripts/setup_usage_api_gateway.py

Requires AWS credentials for the suigetsukan account.
"""

import json
import os
import sys
import time
from typing import cast

import boto3
from botocore.exceptions import ClientError

REGION = os.environ.get("AWS_REGION", "us-west-1")
API_NAME = "UsageAPI"
PARENT_PATH_PART = "usage"
PROXY_PATH_PART = "{proxy+}"
STAGE_NAME = "prod1"
LAMBDA_FUNCTION_NAME = "suigetsukan-usage-rest-api"
LAMBDA_STATEMENT_ID = "UsageAPIInvoke"

CORS_HEADERS = "'Content-Type,Authorization,X-Amz-Date,X-Api-Key,X-Amz-Security-Token'"
CORS_METHODS = "'GET,OPTIONS'"
CORS_ORIGIN = "'*'"


def _get_account_id(session: boto3.Session) -> str:
    return cast(str, session.client("sts").get_caller_identity()["Account"])


def _find_api(apigw, name: str) -> dict | None:
    paginator = apigw.get_paginator("get_rest_apis")
    for page in paginator.paginate():
        for item in page["items"]:
            if item["name"] == name:
                return cast(dict, item)
    return None


def _ensure_api(apigw) -> str:
    api = _find_api(apigw, API_NAME)
    if api:
        print(f"  Found existing {API_NAME} (id={api['id']})")
        return cast(str, api["id"])
    print(f"  Creating REST API: {API_NAME}")
    resp = apigw.create_rest_api(
        name=API_NAME,
        description="Read-only CloudWatch RUM usage stats for the admin Statistics page.",
        endpointConfiguration={"types": ["REGIONAL"]},
    )
    return cast(str, resp["id"])


def _get_resources(apigw, api_id: str) -> dict[str, dict]:
    by_path: dict[str, dict] = {}
    paginator = apigw.get_paginator("get_resources")
    for page in paginator.paginate(restApiId=api_id):
        for item in page["items"]:
            by_path[item["path"]] = item
    return by_path


def _ensure_resource(apigw, api_id: str, parent_id: str, path_part: str, full_path: str) -> str:
    by_path = _get_resources(apigw, api_id)
    if full_path in by_path:
        return cast(str, by_path[full_path]["id"])
    print(f"  Creating resource: {full_path}")
    resp = apigw.create_resource(restApiId=api_id, parentId=parent_id, pathPart=path_part)
    return cast(str, resp["id"])


def _put_proxy_any(apigw, api_id: str, resource_id: str, lambda_arn: str, region: str) -> None:
    try:
        apigw.delete_method(restApiId=api_id, resourceId=resource_id, httpMethod="ANY")
        print("  Removed existing ANY method")
    except ClientError as err:
        if err.response["Error"]["Code"] != "NotFoundException":
            raise
    apigw.put_method(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="ANY",
        authorizationType="NONE",
        requestParameters={"method.request.path.proxy": True},
    )
    uri = f"arn:aws:apigateway:{region}:lambda:path/2015-03-31/functions/{lambda_arn}/invocations"
    apigw.put_integration(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="ANY",
        type="AWS_PROXY",
        integrationHttpMethod="POST",
        uri=uri,
        passthroughBehavior="WHEN_NO_MATCH",
        contentHandling="CONVERT_TO_TEXT",
    )
    print("  Wired ANY → AWS_PROXY → Lambda")


def _put_options_mock(apigw, api_id: str, resource_id: str) -> None:
    try:
        apigw.delete_method(restApiId=api_id, resourceId=resource_id, httpMethod="OPTIONS")
    except ClientError as err:
        if err.response["Error"]["Code"] != "NotFoundException":
            raise
    apigw.put_method(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="OPTIONS",
        authorizationType="NONE",
    )
    apigw.put_method_response(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="OPTIONS",
        statusCode="200",
        responseParameters={
            "method.response.header.Access-Control-Allow-Headers": False,
            "method.response.header.Access-Control-Allow-Methods": False,
            "method.response.header.Access-Control-Allow-Origin": False,
        },
        responseModels={"application/json": "Empty"},
    )
    apigw.put_integration(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="OPTIONS",
        type="MOCK",
        requestTemplates={"application/json": '{"statusCode": 200}'},
        passthroughBehavior="WHEN_NO_MATCH",
    )
    apigw.put_integration_response(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="OPTIONS",
        statusCode="200",
        responseParameters={
            "method.response.header.Access-Control-Allow-Headers": CORS_HEADERS,
            "method.response.header.Access-Control-Allow-Methods": CORS_METHODS,
            "method.response.header.Access-Control-Allow-Origin": CORS_ORIGIN,
        },
    )
    print("  Wired OPTIONS → MOCK with CORS headers")


def _grant_lambda_invoke(lam, account_id: str, api_id: str, region: str) -> None:
    source_arn = f"arn:aws:execute-api:{region}:{account_id}:{api_id}/*/*/{PARENT_PATH_PART}/*"
    try:
        lam.remove_permission(
            FunctionName=LAMBDA_FUNCTION_NAME,
            StatementId=LAMBDA_STATEMENT_ID,
        )
    except ClientError as err:
        if err.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
    lam.add_permission(
        FunctionName=LAMBDA_FUNCTION_NAME,
        StatementId=LAMBDA_STATEMENT_ID,
        Action="lambda:InvokeFunction",
        Principal="apigateway.amazonaws.com",
        SourceArn=source_arn,
    )
    print(f"  Granted API Gateway invoke permission on {LAMBDA_FUNCTION_NAME}")


def _deploy(apigw, api_id: str) -> None:
    apigw.create_deployment(restApiId=api_id, stageName=STAGE_NAME)
    print(f"  Deployed to stage: {STAGE_NAME}")


def main() -> int:
    session = boto3.Session(region_name=REGION)
    account_id = _get_account_id(session)
    apigw = session.client("apigateway")
    lam = session.client("lambda")

    try:
        fn = lam.get_function(FunctionName=LAMBDA_FUNCTION_NAME)
    except ClientError as err:
        if err.response["Error"]["Code"] == "ResourceNotFoundException":
            print(
                f"ERROR: Lambda {LAMBDA_FUNCTION_NAME} not deployed yet. "
                "Wait for the GitHub Actions pipeline to publish it, then re-run.",
                file=sys.stderr,
            )
            return 1
        raise
    lambda_arn = fn["Configuration"]["FunctionArn"]
    print(f"  Lambda: {lambda_arn}")

    api_id = _ensure_api(apigw)
    by_path = _get_resources(apigw, api_id)
    root_id = by_path["/"]["id"]
    parent_resource_id = _ensure_resource(
        apigw, api_id, root_id, PARENT_PATH_PART, f"/{PARENT_PATH_PART}"
    )
    proxy_resource_id = _ensure_resource(
        apigw,
        api_id,
        parent_resource_id,
        PROXY_PATH_PART,
        f"/{PARENT_PATH_PART}/{PROXY_PATH_PART}",
    )

    _put_proxy_any(apigw, api_id, proxy_resource_id, lambda_arn, REGION)
    _put_options_mock(apigw, api_id, proxy_resource_id)
    _grant_lambda_invoke(lam, account_id, api_id, REGION)
    _deploy(apigw, api_id)

    # Brief settle pause so the invoke URL is immediately usable.
    time.sleep(3)
    invoke_url = f"https://{api_id}.execute-api.{REGION}.amazonaws.com/{STAGE_NAME}"
    print()
    print("=" * 60)
    print(f"  UsageAPI invoke URL: {invoke_url}")
    print(f"  Sample: curl {invoke_url}/usage/summary")
    print("=" * 60)
    print()
    print(json.dumps({"invoke_url": invoke_url, "api_id": api_id}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
