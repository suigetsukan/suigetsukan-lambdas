"""
This is a REST wrapper for AWS Billing
"""

import calendar
import json
import os
from datetime import datetime, timedelta, date

import boto3

from common.constants import (
    CE_GRANULARITY_MONTHLY,
    CE_METRIC_BLENDED_COST,
    CE_METRIC_BLENDED_COST_FORECAST,
    CE_PREDICTION_INTERVAL_LEVEL,
    CORS_HEADERS_ALL,
    CORS_METHODS_GET_OPTIONS,
    CORS_ORIGIN_ALL,
    DEFAULT_REGION,
    HTTP_OK,
    HTTP_UNAUTHORIZED,
)


def _cors_origin():
    """CORS origin from env; restrict via CORS_ALLOWED_ORIGIN for production."""
    return os.environ.get("CORS_ALLOWED_ORIGIN", CORS_ORIGIN_ALL)


def _require_authorizer(event):
    """Require API Gateway authorizer when event looks like API Gateway request."""
    http_method = event.get("httpMethod")
    if (
        http_method
        and http_method != "OPTIONS"
        and not event.get("requestContext", {}).get("authorizer")
    ):
        return {
            "statusCode": HTTP_UNAUTHORIZED,
            "headers": {
                "Access-Control-Allow-Origin": _cors_origin(),
                "Access-Control-Allow-Headers": CORS_HEADERS_ALL,
                "Access-Control-Allow-Methods": CORS_METHODS_GET_OPTIONS,
            },
            "body": json.dumps({"error": "Unauthorized: API Gateway must use authorizer"}),
        }
    return None


def set_leading_zero(number):
    """
    Set leading zero for number if it is one-digit
    :param number: number with possible leading zero
    :return: number with leading zero if it is one digit
    :rtype: str
    """
    if len(str(number)) == 1:
        number = "0" + str(number)
    return number


def get_cost_and_usage(client, year, month):
    """
    Get AWS cost and usage for a given year/month

    :param client: The Cost Explorer client
    :param year: The year
    :param month: The month
    :return: The cost and usage
    :rtype: float
    """
    last_day = calendar.monthrange(year, month)[1]
    month = set_leading_zero(month)
    first = str(year) + "-" + str(month) + "-" + "01"
    last = str(year) + "-" + str(month) + "-" + str(last_day)

    response = client.get_cost_and_usage(
        TimePeriod={"Start": first, "End": last},
        Granularity=CE_GRANULARITY_MONTHLY,
        Metrics=[CE_METRIC_BLENDED_COST],
    )
    if response["ResponseMetadata"]["HTTPStatusCode"] != HTTP_OK:
        raise RuntimeError("Error in get_cost_and_usage_response")
    results = response.get("ResultsByTime", [])
    if not results:
        return 0.0
    cost = results[0]["Total"]["BlendedCost"]["Amount"]
    return round(float(cost), 2)


def get_cost_forecast(client):
    """
     Get AWS cost forecast for the current month
    :param client: The Cost Explorer client
    :return: The cost forecast
    :rtype: float
    """

    current_year = datetime.now().year
    current_month = datetime.now().month
    current_day = datetime.now().day
    last_day = calendar.monthrange(current_year, current_month)[1]
    current_month = set_leading_zero(current_month)
    current_day = set_leading_zero(current_day)

    first = str(current_year) + "-" + str(current_month) + "-" + str(current_day)
    last = str(current_year) + "-" + str(current_month) + "-" + str(last_day)
    if current_day != last_day:
        response = client.get_cost_forecast(
            TimePeriod={"Start": first, "End": last},
            Granularity=CE_GRANULARITY_MONTHLY,
            Metric=CE_METRIC_BLENDED_COST_FORECAST,
            PredictionIntervalLevel=CE_PREDICTION_INTERVAL_LEVEL,
        )
        if response["ResponseMetadata"]["HTTPStatusCode"] != HTTP_OK:
            raise RuntimeError("Error in get_usage_forecast_response")
        cost = response["Total"]["Amount"]
    else:
        cost = 0.0
    return round(float(cost), 2)


def get_previous_month_cost(client):
    """
    Get AWS cost for the previous month
    :param client: The Cost Explorer client
    :return: The cost for the previous month
    :rtype: float
    """
    today = date.today()
    first = today.replace(day=1)
    last_month = first - timedelta(days=1)
    year = last_month.year
    month = last_month.month
    return get_cost_and_usage(client, year, month)


def get_this_month_cost(client):
    """
    Get AWS cost for the current month
    :param client: The Cost Explorer client
    :return: The cost for the current month
    :rtype: float
    """
    current_year = datetime.now().year
    current_month = datetime.now().month
    return get_cost_and_usage(client, current_year, current_month)


def lambda_handler(event, context):
    """
    The main lambda handler function
    :param event: The event from the ether
    :param context: THe runtime context
    :return:  The query response
    :rtype: dict
    """
    print(event)
    auth_err = _require_authorizer(event)
    if auth_err:
        return auth_err
    region = os.environ.get("AWS_REGION", DEFAULT_REGION)
    client = boto3.client("ce", region_name=region)

    body = {
        "this_month": get_this_month_cost(client),
        "last_month": get_previous_month_cost(client),
        "forecast": get_cost_forecast(client),
    }

    # This goes to the CloudWatch Logs
    print(body)

    return {
        "statusCode": HTTP_OK,
        "headers": {
            "Access-Control-Allow-Origin": _cors_origin(),
            "Access-Control-Allow-Headers": CORS_HEADERS_ALL,
            "Access-Control-Allow-Methods": CORS_METHODS_GET_OPTIONS,
        },
        "body": json.dumps(body),
    }
