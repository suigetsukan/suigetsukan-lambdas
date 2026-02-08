#!/usr/bin/env python3
"""
Setup Lambda triggers based on config.json event_sources.
"""

import json
import os
import sys
import time

import boto3
from botocore.exceptions import ClientError

REPO_ROOT = os.getcwd()
LAMBDAS_DIR = os.path.join(REPO_ROOT, "lambdas")
REGION = os.getenv("AWS_REGION", "us-west-1")
ACCOUNT_ID = os.getenv("AWS_ACCOUNT_ID")

DEPLOY_ALL = os.getenv("DEPLOY_ALL", "false").lower() == "true"
CHANGED_LAMBDAS_JSON = os.getenv("CHANGED_LAMBDAS", "[]")

lambda_client = boto3.client("lambda", region_name=REGION)
events_client = boto3.client("events", region_name=REGION)
iam_client = boto3.client("iam", region_name=REGION)


def ensure_iot_error_role():
    role_name = "suigetsukan-iot-rule-error-role"
    try:
        iam_client.get_role(RoleName=role_name)
        print(f"Error role {role_name} exists")
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            print(f"Creating error role {role_name}")
            trust_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "iot.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }
            iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="Role for IoT rule error actions",
            )
            policy_doc = {
                "Version": "2012-10-17",
                "Statement": [{"Effect": "Allow", "Action": "iot:Publish", "Resource": "*"}],
            }
            iam_client.put_role_policy(
                RoleName=role_name,
                PolicyName="IoTPublishPolicy",
                PolicyDocument=json.dumps(policy_doc),
            )
            time.sleep(30)
        else:
            raise


def get_lambda_dirs():
    if not os.path.exists(LAMBDAS_DIR):
        return []
    all_dirs = [p for p in os.listdir(LAMBDAS_DIR) if os.path.isdir(os.path.join(LAMBDAS_DIR, p))]
    if DEPLOY_ALL:
        return all_dirs
    try:
        changed = set(json.loads(CHANGED_LAMBDAS_JSON))
    except json.JSONDecodeError:
        changed = set()
    return [d for d in all_dirs if d in changed]


def load_config(lambda_name):
    config_path = os.path.join(LAMBDAS_DIR, lambda_name, "config.json")
    if not os.path.exists(config_path):
        return {}
    with open(config_path) as f:
        return json.load(f)


def get_lambda_arn(function_name):
    response = lambda_client.get_function(FunctionName=function_name)
    return response["Configuration"]["FunctionArn"]


def add_lambda_permission(lambda_arn, source_arn, statement_id):
    try:
        lambda_client.add_permission(
            FunctionName=lambda_arn,
            StatementId=statement_id,
            Action="lambda:InvokeFunction",
            Principal="events.amazonaws.com",
            SourceArn=source_arn,
        )
        print(f"Added permission {statement_id} to {lambda_arn}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            print(f"Permission {statement_id} already exists for {lambda_arn}")
        else:
            raise


def setup_sqs_trigger(config, lambda_arn):
    for source in config.get("event_sources", []):
        if source["type"] == "sqs":
            queue_arn = source["arn"]
            batch_size = source.get("batch_size", 10)
            response = lambda_client.list_event_source_mappings(FunctionName=lambda_arn)
            for mapping in response["EventSourceMappings"]:
                if mapping["EventSourceArn"] == queue_arn:
                    print(f"SQS mapping already exists for {queue_arn} to {lambda_arn}")
                    return
            lambda_client.create_event_source_mapping(
                EventSourceArn=queue_arn,
                FunctionName=lambda_arn,
                Enabled=True,
                BatchSize=batch_size,
            )
            print(f"Created SQS mapping for {queue_arn} to {lambda_arn}")


def setup_iot_rule_trigger(config, lambda_arn):
    iot_client = boto3.client("iot", region_name=REGION)
    for source in config.get("event_sources", []):
        if source["type"] == "iot-rule":
            topic = source["topic"]
            rule_name = (
                source.get("rule_name") or f"suigetsukan-{config['function_name_suffix']}-IoTRule"
            )
            rule_name = rule_name.replace("-", "_")
            actions = source["actions"]
            ensure_iot_error_role()
            rule_exists = True
            try:
                iot_client.get_topic_rule(ruleName=rule_name)
                print(f"IoT Rule {rule_name} exists – updating")
            except ClientError as e:
                if e.response["Error"]["Code"] in [
                    "ResourceNotFoundException",
                    "UnauthorizedException",
                ]:
                    rule_exists = False
                    print(f"IoT Rule {rule_name} does not exist – attempting creation")
                else:
                    raise
            payload = {
                "sql": f"SELECT * FROM '{topic}'",
                "actions": [
                    {
                        "lambda": {
                            "functionArn": f"arn:aws:lambda:{REGION}:{ACCOUNT_ID}:function:{act['function_name']}"
                            if act["type"] == "lambda"
                            else lambda_arn
                        }
                    }
                    for act in actions
                ],
                "errorAction": {
                    "republish": {
                        "roleArn": f"arn:aws:iam::{ACCOUNT_ID}:role/suigetsukan-iot-rule-error-role",
                        "topic": "error/rule-errors",
                    }
                },
            }
            if rule_exists:
                iot_client.replace_topic_rule(ruleName=rule_name, topicRulePayload=payload)
            else:
                iot_client.create_topic_rule(ruleName=rule_name, topicRulePayload=payload)
            add_lambda_permission(
                lambda_arn,
                f"arn:aws:iot:{REGION}:{ACCOUNT_ID}:rule/{rule_name}",
                f"AllowIoT-{rule_name}",
            )
            time.sleep(5)


def setup_eventbridge_trigger(config, lambda_arn):
    for source in config.get("event_sources", []):
        if source["type"] == "eventbridge":
            event_bus = source["arn"]
            event_pattern = source["event_pattern"]
            rule_name = (
                source.get("rule_name") or f"suigetsukan-{config['function_name_suffix']}-Rule"
            )
            if event_bus == "default":
                event_bus_arn = f"arn:aws:events:{REGION}:{ACCOUNT_ID}:event-bus/default"
            else:
                event_bus_arn = event_bus
            events_client.put_rule(
                Name=rule_name,
                EventPattern=event_pattern,
                State="ENABLED",
                EventBusName=event_bus_arn,
                Description=f"Rule for {config.get('function_name', '')}",
            )
            rule_arn = f"arn:aws:events:{REGION}:{ACCOUNT_ID}:rule/{rule_name}"
            add_lambda_permission(lambda_arn, rule_arn, f"AllowEventBridge-{rule_name}")
            target_id = "1"
            try:
                existing = events_client.list_targets_by_rule(
                    Rule=rule_name, EventBusName=event_bus_arn
                )
                if existing.get("Targets"):
                    events_client.remove_targets(
                        Rule=rule_name,
                        EventBusName=event_bus_arn,
                        Ids=[t["Id"] for t in existing["Targets"]],
                    )
            except ClientError:
                pass
            events_client.put_targets(
                Rule=rule_name,
                EventBusName=event_bus_arn,
                Targets=[{"Id": target_id, "Arn": lambda_arn}],
            )
            time.sleep(5)


def main():
    if not ACCOUNT_ID:
        print("ERROR: AWS_ACCOUNT_ID environment variable required")
        sys.exit(1)
    lambda_dirs = get_lambda_dirs()
    if not lambda_dirs:
        print("No Lambdas to configure triggers for")
        return
    for lambda_name in sorted(lambda_dirs):
        config = load_config(lambda_name)
        if not config.get("event_sources"):
            continue
        function_name = config.get("function_name") or f"suigetsukan-{lambda_name}"
        lambda_arn = get_lambda_arn(function_name)
        setup_sqs_trigger(config, lambda_arn)
        setup_eventbridge_trigger(config, lambda_arn)
        setup_iot_rule_trigger(config, lambda_arn)
    print("Trigger setup complete!")


if __name__ == "__main__":
    main()
