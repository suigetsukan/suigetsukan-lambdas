#!/usr/bin/env bash
#
# Run the analytics-report Lambda locally for testing.
# Queries live Pinpoint data and publishes to the SNS topic.
#
# Usage:
#   scripts/run_analytics_report.sh [--dry-run]
#
# Options:
#   --dry-run   Print the report to stdout instead of publishing to SNS.
#
# Requires:
#   - AWS credentials via the tennis@suigetsukan profile
#   - Python 3.11+
#   - boto3 installed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

AWS_PROFILE="tennis@suigetsukan"
AWS_REGION="us-west-1"
PINPOINT_APP_ID="e9b888ad9b1144c380ec4e684b0a3a85"
PINPOINT_REGION="us-west-2"
SNS_TOPIC_ARN="arn:aws:sns:us-west-1:463840431592:suigetsukan-analytics-report"

DRY_RUN="${1:-}"

export AWS_PROFILE
export AWS_REGION
export AWS_PINPOINT_APP_ID="$PINPOINT_APP_ID"
export AWS_PINPOINT_REGION="$PINPOINT_REGION"
export AWS_SNS_ANALYTICS_TOPIC_ARN="$SNS_TOPIC_ARN"
export PYTHONPATH="$REPO_ROOT"

if [ "$DRY_RUN" = "--dry-run" ]; then
    echo "=== DRY RUN — report will be printed, not emailed ==="
    echo ""
    python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location(
    'app', '${REPO_ROOT}/lambdas/analytics-report/app.py'
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Monkey-patch SNS publish to just print
import boto3
real_client = boto3.client
def patched_client(service, **kwargs):
    client = real_client(service, **kwargs)
    if service == 'sns':
        original_publish = client.publish
        def fake_publish(**pk):
            print(pk.get('Message', ''))
            return {'MessageId': 'dry-run'}
        client.publish = fake_publish
    return client
boto3.client = patched_client

result = mod.lambda_handler({}, None)
print()
print(f'Period: {result[\"report_period\"]}')
"
else
    echo "Running analytics report (live — will email via SNS)..."
    python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location(
    'app', '${REPO_ROOT}/lambdas/analytics-report/app.py'
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
result = mod.lambda_handler({}, None)
print(f'Done. Report published for: {result[\"report_period\"]}')
"
fi
