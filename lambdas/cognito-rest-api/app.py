"""
This is a REST API lambda for accessing Cognito functions
"""
#  Copyright (c) 2023.  Suigetsukan Dojo

import json
import logging
import os

import boto3

from common.constants import (
    CHARSET_UTF8,
    COGNITO_GROUP_ADMIN,
    COGNITO_GROUP_APPROVED,
    COGNITO_GROUP_UNAPPROVED,
    CORS_HEADERS_ALL,
    CORS_METHODS_GET_POST_OPTIONS,
    CORS_ORIGIN_ALL,
    DEFAULT_REGION,
    HTTP_BAD_REQUEST,
    HTTP_NO_CONTENT,
    HTTP_OK,
    HTTP_UNAUTHORIZED,
)

logger = logging.getLogger(__name__)


def _cors_origin():
    """CORS origin from env; restrict via CORS_ALLOWED_ORIGIN for production."""
    return os.environ.get("CORS_ALLOWED_ORIGIN", CORS_ORIGIN_ALL)


def _error_response(status_code, message):
    """Return a standardized error response with CORS headers."""
    return {
        "statusCode": status_code,
        "headers": {
            "Access-Control-Allow-Origin": _cors_origin(),
            "Access-Control-Allow-Headers": CORS_HEADERS_ALL,
            "Access-Control-Allow-Methods": CORS_METHODS_GET_POST_OPTIONS,
        },
        "body": json.dumps({"error": message}),
    }


def _require_authorizer(event):
    """Require API Gateway Cognito authorizer; return 401 if missing."""
    if not event.get("requestContext", {}).get("authorizer"):
        return _error_response(
            HTTP_UNAUTHORIZED,
            "Unauthorized: API Gateway must use Cognito authorizer",
        )
    return None


def _options_response():
    """Return CORS preflight response."""
    return {
        "statusCode": HTTP_NO_CONTENT,
        "headers": {
            "Access-Control-Allow-Origin": _cors_origin(),
            "Access-Control-Allow-Headers": CORS_HEADERS_ALL,
            "Access-Control-Allow-Methods": CORS_METHODS_GET_POST_OPTIONS,
        },
        "body": "",
    }


def _success_response(body):
    """Return successful JSON response with CORS headers."""
    return {
        "statusCode": HTTP_OK,
        "headers": {
            "Access-Control-Allow-Origin": _cors_origin(),
            "Access-Control-Allow-Headers": CORS_HEADERS_ALL,
            "Access-Control-Allow-Methods": CORS_METHODS_GET_POST_OPTIONS,
        },
        "body": json.dumps(body),
    }


def _handle_get(event, client, user_pool_id):
    """Handle GET requests; return response body."""
    path = event["path"]
    if path == "/list":
        return list_handler(client, user_pool_id)
    if path == "/list/admin":
        return get_admin_users(client, user_pool_id)
    raise RuntimeError(f"Invalid GET path: {path}")


def _handle_post(event, client, user_pool_id):
    """Handle POST requests; return response body."""
    body_raw = event.get("body")
    if body_raw is None or body_raw == "":
        return _error_response(HTTP_BAD_REQUEST, "Missing body")
    try:
        data = json.loads(body_raw)
    except json.JSONDecodeError:
        return _error_response(HTTP_BAD_REQUEST, "Invalid JSON")
    required = ["user", "user_email", "admin_email"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        return _error_response(HTTP_BAD_REQUEST, f"Missing required fields: {', '.join(missing)}")

    user_name = data["user"]
    user_email = data["user_email"]
    actor = data["admin_email"]
    admin_users = get_admin_users(client, user_pool_id)
    path = event["path"]

    post_handlers = {
        "/approve": (approve_handler, "APPROVED", f"has been approved by {actor}"),
        "/promote": (promote_handler, "PROMOTED", f"promoted to administrator by {actor}"),
        "/close": (close_handler, "CLOSED", "account closed by user request"),
        "/deny": (deny_handler, "DENIED", f"has been denied by {actor}"),
        "/delete": (delete_handler, "DELETED", f"has been deleted by {actor}"),
    }
    for suffix, (handler_fn, subject, msg) in post_handlers.items():
        if path.endswith(suffix):
            body = handler_fn(user_name, client, user_pool_id)
            send_mail(
                admin_users,
                f"{subject}: {user_email}",
                f"User {user_email} {msg}",
            )
            return body
    raise RuntimeError("Invalid POST path: " + path)


def compile_users(resp):
    """
    Take the output from Cognito and return a list of users and their emails
    :param resp: Cognito response
    :return: List of users and their emails
    """
    return_var = []
    for user in resp["Users"]:
        user_name = user["Username"]
        for attribute in user["Attributes"]:
            if attribute["Name"] == "email":
                email = attribute["Value"]
                new_user = {"user_name": user_name, "email": email}
                return_var.append(new_user)
    return return_var


def get_users_in_group(client, USER_POOL_ID, group_name):
    """
    Get the users in a Cognito user group
    :param client: Cognito client
    :param USER_POOL_ID: Cognito user pool id
    :param group_name: Cognito group name
    :return: List of users in the group
    """
    response = client.list_users_in_group(UserPoolId=USER_POOL_ID, GroupName=group_name)

    if response["ResponseMetadata"]["HTTPStatusCode"] != HTTP_OK:
        raise RuntimeError("An error occurred retrieving the users.")

    return compile_users(response)


def add_user_to_group(client, USER_POOL_ID, user_name, group_name):
    """
    Add a user to a Cognito user group

    :param client: Cognito client
    :param USER_POOL_ID: Cognito user pool id
    :param user_name: Cognito user name
    :param group_name: Cognito group name
    :return: Nothing
    """
    response = client.admin_add_user_to_group(
        UserPoolId=USER_POOL_ID, Username=user_name, GroupName=group_name
    )

    # Check the response
    if response["ResponseMetadata"]["HTTPStatusCode"] != HTTP_OK:
        raise RuntimeError(f"Error adding user {user_name} to group {group_name}.")

    return


def remove_user_from_group(client, USER_POOL_ID, user_name, group_name):
    """
     Remove a user from a Cognito user group
    :param client: Cognito client
    :param USER_POOL_ID: Cognito user pool id
    :param user_name: Cognito user name
    :param group_name: Cognito group name
    :return: Nothing
    """
    response = client.admin_remove_user_from_group(
        UserPoolId=USER_POOL_ID, Username=user_name, GroupName=group_name
    )

    # Check the response
    if response["ResponseMetadata"]["HTTPStatusCode"] != HTTP_OK:
        raise RuntimeError("An error occurred removing the user from the group.")

    return


def delete_user_completely(client, USER_POOL_ID, user_name):
    """
    Delete a Cognito user
    :param client: Cognito client
    :param USER_POOL_ID: Cognito user pool id
    :param user_name: Cognito user name
    :return: Nothing
    """
    response = client.admin_delete_user(UserPoolId=USER_POOL_ID, Username=user_name)

    # Check the response
    if response["ResponseMetadata"]["HTTPStatusCode"] != HTTP_OK:
        raise RuntimeError("An error occurred deleting the user.")

    return


def send_mail(addresses, subject, body):
    """
    Email a list of addresses
    :param addresses: List of email addresses
    :param subject: Subject of the email
    :param body: Body of the email
    :return: Nothing
    """
    ses_region = os.environ.get("SES_REGION", os.environ.get("AWS_REGION", DEFAULT_REGION))
    ses_client = boto3.client("ses", region_name=ses_region)
    SES_SOURCE_EMAIL = os.environ["AWS_SES_SOURCE_EMAIL"]

    response = ses_client.send_email(
        Destination={
            "ToAddresses": addresses,
        },
        Message={
            "Body": {
                "Text": {
                    "Charset": CHARSET_UTF8,
                    "Data": body,
                },
            },
            "Subject": {
                "Charset": CHARSET_UTF8,
                "Data": subject,
            },
        },
        Source=SES_SOURCE_EMAIL,
    )
    if response["ResponseMetadata"]["HTTPStatusCode"] != HTTP_OK:
        raise RuntimeError(f"An error occurred sending email to {addresses}.")

    return


def get_all_users(client, USER_POOL_ID):
    """
    Get all users in all Cognito user pools
    :param client: Cognito client
    :param USER_POOL_ID: Cognito user pool id
    :return: List of users
    """
    response = client.list_users(UserPoolId=USER_POOL_ID)

    if response["ResponseMetadata"]["HTTPStatusCode"] != HTTP_OK:
        raise RuntimeError("An error occurred retrieving all users.")

    return compile_users(response)


def compile_emails(resp):
    """
    Take the output from Cognito and return a list of users and their emails
    :param resp: Cognito response
    :return: List of users and their emails
    """
    admin_emails = []
    for user in resp["Users"]:
        for attribute in user["Attributes"]:
            if attribute["Name"] == "email":
                email = attribute["Value"]
                admin_emails.append(email)

    if not admin_emails:
        raise RuntimeError("Error: No email addresses found")

    return admin_emails


def get_admin_users(client, USER_POOL_ID):
    """
    Get admin users in Cognito user pool
    :param client: Cognito client
    :param USER_POOL_ID: Cognito user pool id
    :return: List of users
    """
    response = client.list_users_in_group(UserPoolId=USER_POOL_ID, GroupName=COGNITO_GROUP_ADMIN)

    if response["ResponseMetadata"]["HTTPStatusCode"] != HTTP_OK:
        raise RuntimeError("An error occurred retrieving the admin users.")

    return compile_emails(response)


def list_handler(client, USER_POOL_ID):
    """
    List all Cognito users
    :param client: Cognito client
    :param USER_POOL_ID: Cognito user pool id
    :return: JSON object of users
    """
    all_users = get_all_users(client, USER_POOL_ID)
    unapproved_users = get_users_in_group(client, USER_POOL_ID, COGNITO_GROUP_UNAPPROVED)
    approved_users = get_users_in_group(client, USER_POOL_ID, COGNITO_GROUP_APPROVED)
    unapproved_set = {(u["user_name"], u["email"]) for u in unapproved_users}
    approved_set = {(u["user_name"], u["email"]) for u in approved_users}
    other_users = [
        u
        for u in all_users
        if (u["user_name"], u["email"]) not in unapproved_set
        and (u["user_name"], u["email"]) not in approved_set
    ]
    logger.debug(
        "List: %d approved, %d unapproved, %d other",
        len(approved_users),
        len(unapproved_users),
        len(other_users),
    )

    body = {
        "approved": sorted(approved_users, key=lambda x: x["email"]),
        "unapproved": unapproved_users,
    }
    return body


def approve_handler(user_name, client, USER_POOL_ID):
    """
    Approve a Cognito user
    :param user_name: Cognito user name
    :param client: Cognito client
    :param USER_POOL_ID: Cognito user pool id
    :return: JSON object
    """
    body = None
    users = get_users_in_group(client, USER_POOL_ID, COGNITO_GROUP_UNAPPROVED)
    for user in users:
        if user["user_name"] == user_name:
            remove_user_from_group(client, USER_POOL_ID, user_name, COGNITO_GROUP_UNAPPROVED)
            add_user_to_group(client, USER_POOL_ID, user_name, COGNITO_GROUP_APPROVED)
            email = user["email"]
            send_mail(
                [email],
                "Suigetsukan Curriculum Account Approved!",
                "Congratulations! Your account has been approved by Sensei.",
            )
            body = f"The user {email} has been approved"
            logger.info("Approval email sent")
            break

    if not body:
        raise RuntimeError("User not found. Could not approve.")

    return body


def promote_handler(user_name, client, USER_POOL_ID):
    """
    Promote a Cognito user
    :param user_name: Cognito user name
    :param client: Cognito client
    :param USER_POOL_ID: Cognito user pool id
    :return: JSON object
    """
    body = None
    users = get_users_in_group(client, USER_POOL_ID, COGNITO_GROUP_APPROVED)
    for user in users:
        if user["user_name"] == user_name:
            add_user_to_group(client, USER_POOL_ID, user_name, COGNITO_GROUP_ADMIN)
            email = user["email"]
            send_mail(
                [email],
                "Suigetsukan Curriculum Account Promoted!",
                "Congratulations! Your account has been promoted to Administrator.",
            )
            body = f"The user {email} has been promoted"
            logger.info("Promotion email sent")
            break

    if not body:
        raise RuntimeError("User not found. Could not promote.")

    return body


def deny_handler(user_name, client, USER_POOL_ID):
    """
    Deny access to a Cognito user
    :param user_name: Cognito user name
    :param client: Cognito client
    :param USER_POOL_ID: Cognito user pool id
    :return: JSON object
    """
    users = get_users_in_group(client, USER_POOL_ID, COGNITO_GROUP_UNAPPROVED)
    body = None
    for user in users:
        if user["user_name"] == user_name:
            delete_user_completely(client, USER_POOL_ID, user_name)
            email = user["email"]
            send_mail(
                [email],
                "Suigetsukan Curriculum Account Denied!",
                "We are sorry! Access to Suigetsukan Curriculum has been denied.",
            )
            body = f"The user {email} has been denied access"
            logger.info("Denial email sent")
            break

    if not body:
        raise RuntimeError("User not found for denial")

    return body


def close_handler(user_name, client, USER_POOL_ID):
    """
    Close a Cognito user account
    :param user_name: Cognito user name
    :param client: Cognito client
    :param USER_POOL_ID: Cognito user pool id
    :return: JSON object
    """
    users = get_all_users(client, USER_POOL_ID)
    body = None
    for user in users:
        if user["user_name"] == user_name:
            delete_user_completely(client, USER_POOL_ID, user_name)
            email = user["email"]
            send_mail(
                [email],
                "Suigetsukan Curriculum Account Closed!",
                "Account closed. We are sorry to see you go!",
            )
            body = f"The user {email} account has been closed"
            logger.info("Account closure email sent")
            break

    if not body:
        raise RuntimeError("User not found for closure")

    return body


def delete_handler(user_name, client, USER_POOL_ID):
    """
     Delete a Cognito user

    :param user_name: Cognito user name
    :param client: Cognito client
    :param USER_POOL_ID: Cognito user pool id
    :return:  Nothing
    """
    users = get_all_users(client, USER_POOL_ID)
    email = None
    for user in users:
        if user["user_name"] == user_name:
            email = user["email"]
            break
    if not email:
        raise RuntimeError("User not found for deletion")
    delete_user_completely(client, USER_POOL_ID, user_name)
    send_mail(
        [email],
        "Suigetsukan Curriculum Account Deleted!",
        "Your account has been deleted by the administrator",
    )
    logger.info("Deletion email sent")
    return f"The user {email} account has been deleted"


def handler(event, context):
    logger.debug("Request received: %s %s", event.get("httpMethod"), event.get("path"))
    if not event.get("httpMethod") or not event.get("path"):
        return _error_response(HTTP_BAD_REQUEST, "Missing httpMethod or path")

    region = os.environ["AWS_REGION"]
    user_pool_id = os.environ["AWS_COGNITO_USER_POOL_ID"]
    client = boto3.client("cognito-idp", region_name=region)

    if event["httpMethod"] == "OPTIONS":
        return _options_response()
    auth_err = _require_authorizer(event)
    if auth_err:
        return auth_err

    if event["httpMethod"] == "GET":
        body = _handle_get(event, client, user_pool_id)
    elif event["httpMethod"] == "POST":
        result = _handle_post(event, client, user_pool_id)
        if isinstance(result, dict) and "statusCode" in result:
            return result  # already a full error response
        body = result
    else:
        raise RuntimeError("Invalid http method: " + event["httpMethod"])

    return _success_response(body)
