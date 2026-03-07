"""
A Cognito Post Confirmation Lambda Trigger function that will be associated with the Cognito User
Pool for the application.

Amazon Cognito invokes this trigger after a new user is confirmed, allowing you to send custom
messages or to add custom logic.
"""

#  Copyright (c) 2023.  Suigetsukan Dojo

import json
import logging
import os
import time

import boto3

from common.constants import (
    COGNITO_GROUP_ADMIN,
    COGNITO_GROUP_UNAPPROVED,
    COGNITO_TRIGGER_POST_CONFIRMATION,
    DEFAULT_REGION,
    HTTP_OK,
)

logger = logging.getLogger(__name__)
REGION = os.environ.get("AWS_REGION", DEFAULT_REGION)


def _dbg(hypothesis_id: str, message: str, data: dict | None = None):
    """Log hypothesis-relevant data to CloudWatch for debugging."""
    payload = {
        "sessionId": "955551",
        "hypothesisId": hypothesis_id,
        "location": "cognito-post-confirmation",
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
    }
    logger.info("dbg %s: %s | %s", hypothesis_id, message, json.dumps(payload.get("data", {})))


def add_user_to_cognito_group(user_pool_id, username, group_name):
    """
    Calls the AWS SDK to add a given user into a Cognito User Pool group.
    """
    cognito_client = boto3.client("cognito-idp", region_name=REGION)
    response = cognito_client.admin_add_user_to_group(
        UserPoolId=user_pool_id, Username=username, GroupName=group_name
    )

    if response["ResponseMetadata"]["HTTPStatusCode"] != HTTP_OK:
        raise RuntimeError(f"An error occurred adding user {username} to group {group_name}.")


def compile_emails(resp):
    """
    Compiles the list of administrator emails of the application.
    """
    admin_emails = []
    for user in resp["Users"]:
        for attribute in user["Attributes"]:
            if attribute["Name"] == "email":
                email = attribute["Value"]
                admin_emails.append(email)

    if not admin_emails:
        # #region agent log
        _dbg("H3", "No admin emails found", {"users_count": len(resp.get("Users", []))})
        # #endregion
        raise RuntimeError("Error: No email addresses found")

    return admin_emails


def get_admin_users(user_pool_id):
    """
    Retrieves the list of administrators of the application.
    """
    cognito_client = boto3.client("cognito-idp", region_name=REGION)

    response = cognito_client.list_users_in_group(
        UserPoolId=user_pool_id, GroupName=COGNITO_GROUP_ADMIN
    )

    if response["ResponseMetadata"]["HTTPStatusCode"] != HTTP_OK:
        # #region agent log
        _dbg(
            "H3",
            "list_users_in_group failed",
            {"status": response["ResponseMetadata"]["HTTPStatusCode"]},
        )
        # #endregion
        raise RuntimeError("An error occurred retrieving the admin users.")

    emails = compile_emails(response)
    # #region agent log
    _dbg("H3", "compile_emails result", {"admin_count": len(emails)})
    # #endregion
    return emails


def inform_administrators(email, pool_id):
    """
    Sends a message to the administrators of the application.
    """
    # #region agent log
    _dbg("H3", "Before get_admin_users", {"pool_id": pool_id})
    # #endregion
    admin_users = get_admin_users(pool_id)
    # #region agent log
    _dbg("H3", "Admin list fetched", {"admin_count": len(admin_users)})
    # #endregion

    ses_region = os.environ.get("SES_REGION", REGION)
    ses_source_email = os.environ.get("AWS_SES_SOURCE_EMAIL", "")
    # #region agent log
    _dbg(
        "H4",
        "Before SES send",
        {
            "ses_region": ses_region,
            "has_source_email": bool(ses_source_email),
            "dest_count": len(admin_users),
        },
    )
    # #endregion

    ses_client = boto3.client("ses", region_name=ses_region)

    response = ses_client.send_email(
        Source=ses_source_email,
        Destination={"ToAddresses": admin_users},
        Message={
            "Subject": {"Data": f"New user: {email}"},
            "Body": {"Text": {"Data": f"New user added to Suigetsukan Curriculum: {email}"}},
        },
    )
    if response["ResponseMetadata"]["HTTPStatusCode"] != HTTP_OK:
        # #region agent log
        _dbg("H4", "SES send failed", {"status": response["ResponseMetadata"]["HTTPStatusCode"]})
        # #endregion
        raise RuntimeError("An error occurred sending the email.")
    # #region agent log
    _dbg("H4", "SES send succeeded", {})
    # #endregion
    return True


def handler(event, _context):
    """
    Gets information about the user, including the username and the type of user created,
    also in which user pool. Then, it adds the user in the proper Cognito User Pool group.
    """
    try:
        # #region agent log
        _dbg(
            "H1",
            "Handler invoked",
            {
                "triggerSource": event.get("triggerSource"),
                "userName": event.get("userName"),
                "userPoolId": event.get("userPoolId"),
            },
        )
        # #endregion

        logger.debug("PostConfirmation trigger invoked")
        if not event.get("triggerSource"):
            # #region agent log
            _dbg("H5", "Missing triggerSource", {"event_keys": list(event.keys())})
            # #endregion
            raise ValueError("Invalid Cognito event: missing triggerSource")
        trigger = event["triggerSource"]
        if trigger == COGNITO_TRIGGER_POST_CONFIRMATION:
            username = event.get("userName")
            user_pool_id = event.get("userPoolId")
            request = event.get("request") or {}
            user_attrs = request.get("userAttributes") or {}
            email = user_attrs.get("email")
            # #region agent log
            _dbg(
                "H2",
                "Pre add_user_to_group",
                {
                    "has_username": bool(username),
                    "has_user_pool_id": bool(user_pool_id),
                    "has_email": bool(email),
                },
            )
            # #endregion
            if not all([username, user_pool_id, email]):
                # #region agent log
                _dbg(
                    "H5",
                    "Missing required fields",
                    {
                        "username": bool(username),
                        "user_pool_id": bool(user_pool_id),
                        "email": bool(email),
                    },
                )
                # #endregion
                raise ValueError(
                    "Invalid Cognito event: missing userName, userPoolId, or "
                    "request.userAttributes.email"
                )
            add_user_to_cognito_group(user_pool_id, username, COGNITO_GROUP_UNAPPROVED)
            # #region agent log
            _dbg("H2", "Post add_user_to_group success", {})
            # #endregion
            inform_administrators(email, user_pool_id)
        else:
            # #region agent log
            _dbg("H1", "Other trigger source, no email sent", {"trigger": trigger})
            # #endregion
            logger.info("Trigger source %s: no action taken", trigger)

        return event
    except Exception as exc:
        # #region agent log
        _dbg(
            "H5",
            "Handler exception",
            {
                "exc_type": type(exc).__name__,
                "exc_msg": str(exc),
            },
        )
        # #endregion
        raise
