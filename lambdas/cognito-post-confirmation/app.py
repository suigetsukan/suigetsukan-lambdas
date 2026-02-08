"""
A Cognito Post Confirmation Lambda Trigger function that will be associated with the Cognito User
Pool for the application.

Amazon Cognito invokes this trigger after a new user is confirmed, allowing you to send custom
messages or to add custom logic.
"""

#  Copyright (c) 2023.  Suigetsukan Dojo

import os

import boto3

from common.constants import (
    COGNITO_GROUP_ADMIN,
    COGNITO_GROUP_UNAPPROVED,
    COGNITO_TRIGGER_POST_CONFIRMATION,
    DEFAULT_REGION,
    HTTP_OK,
)

REGION = os.environ.get("AWS_REGION", DEFAULT_REGION)


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

    return


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
        raise RuntimeError("An error occurred retrieving the admin users.")

    return compile_emails(response)


def inform_administrators(email, pool_id):
    """
    Sends a message to the administrators of the application.
    """
    admin_users = get_admin_users(pool_id)

    ses_region = os.environ.get("SES_REGION", REGION)
    ses_client = boto3.client("ses", region_name=ses_region)
    SES_SOURCE_EMAIL = os.environ["AWS_SES_SOURCE_EMAIL"]

    response = ses_client.send_email(
        Source=SES_SOURCE_EMAIL,
        Destination={"ToAddresses": admin_users},
        Message={
            "Subject": {"Data": f"New user: {email}"},
            "Body": {"Text": {"Data": f"New user added to Suigetsukan Curriculum: {email}"}},
        },
    )
    if response["ResponseMetadata"]["HTTPStatusCode"] != HTTP_OK:
        raise RuntimeError("An error occurred sending the email.")
    return True


def handler(event, context):
    """
    Gets information about the user, including the username and the type of user created,
    also in which user pool. Then, it adds the user in the proper Cognito User Pool group.
    """
    print(event)
    print(context)
    if not event.get("triggerSource"):
        raise ValueError("Invalid Cognito event: missing triggerSource")
    trigger = event["triggerSource"]
    if trigger == COGNITO_TRIGGER_POST_CONFIRMATION:
        username = event.get("userName")
        user_pool_id = event.get("userPoolId")
        request = event.get("request") or {}
        user_attrs = request.get("userAttributes") or {}
        email = user_attrs.get("email")
        if not all([username, user_pool_id, email]):
            raise ValueError(
                "Invalid Cognito event: missing userName, userPoolId, or request.userAttributes.email"
            )
        add_user_to_cognito_group(user_pool_id, username, COGNITO_GROUP_UNAPPROVED)
        inform_administrators(email, user_pool_id)
    else:
        print(f"Trigger source: {trigger}. No action taken")

    return event
