#!/usr/bin/env bash
# Create suigetsukan-lambda-execution-role (fallback execution role for Lambdas).
# IAM is global; region is set for consistency with other AWS usage in this repo.
set -euo pipefail

PROFILE="${AWS_PROFILE:-tennis@suigetsukan}"
REGION="${AWS_REGION:-us-west-1}"
ROLE_NAME="suigetsukan-lambda-execution-role"

export AWS_PROFILE="$PROFILE"
export AWS_REGION="$REGION"

echo "Creating role: $ROLE_NAME (profile=$PROFILE, region=$REGION)"

aws iam create-role \
  --role-name "$ROLE_NAME" \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole"
      }
    ]
  }' \
  --description "Fallback execution role for Suigetsukan Lambdas"

echo "Attaching AWSLambdaBasicExecutionRole..."
aws iam attach-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

echo "Done. Role ARN: arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):role/$ROLE_NAME"
