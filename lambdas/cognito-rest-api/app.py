"""
This is a REST API lambda for accessing Cognito functions
"""
#  Copyright (c) 2023.  Suigetsukan Dojo

import json
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
    HTTP_OK,
)


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
    print("all_users: ", all_users)
    unapproved_users = get_users_in_group(client, USER_POOL_ID, COGNITO_GROUP_UNAPPROVED)
    print("unapproved_users: ", unapproved_users)
    for user in unapproved_users:
        all_users.remove(user)

    approved_users = get_users_in_group(client, USER_POOL_ID, COGNITO_GROUP_APPROVED)
    print("approved_users: ", approved_users)
    for user in approved_users:
        all_users.remove(user)

    print("remaining users: ", all_users)

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
    # print('user_name: ', user_name)
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
            print("approval email sent to: ", email)
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
    # print('user_name: ', user_name)
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
            print("promotion email sent to: ", email)
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
            print("deny email sent to: ", email)
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
            print("close email sent to: ", email)
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

    delete_user_completely(client, USER_POOL_ID, user_name)
    users = get_all_users(client, USER_POOL_ID)
    body = None
    for user in users:
        if user["user_name"] == user_name:
            email = user["email"]
            send_mail(
                [email],
                "Suigetsukan Curriculum Account Deleted!",
                "Your account has been deleted by the administrator",
            )
            body = f"The user {email} account has been deleted"
            print("delete email sent to: ", email)
            break
    return body


def handler(event, context):
    print(event)
    REGION = os.environ["AWS_REGION"]
    USER_POOL_ID = os.environ["AWS_COGNITO_USER_POOL_ID"]

    client = boto3.client("cognito-idp", region_name=REGION)

    if event["httpMethod"] == "GET":
        if event["path"] == "/list":
            body = list_handler(client, USER_POOL_ID)
        elif event["path"] == "/list/admin":
            body = get_admin_users(client, USER_POOL_ID)
        else:
            raise RuntimeError(f"Invalid GET path: {event['path']}")

    elif event["httpMethod"] == "POST":
        admin_users = get_admin_users(client, USER_POOL_ID)

        print("admin_users: ", admin_users)

        data = json.loads(event["body"])
        print("data: ", data)

        user_name = data["user"]
        # print('user_name: ', user_name)
        user_email = data["user_email"]
        # print('user_email: ', user_email)
        actor = data["admin_email"]
        # print('actor: ', actor)

        if "approve" in event["path"]:
            body = approve_handler(user_name, client, USER_POOL_ID)
            send_mail(
                admin_users,
                f"APPROVED: {user_email}",
                f"User {user_email} has been approved by {actor}",
            )
        elif "promote" in event["path"]:
            body = promote_handler(user_name, client, USER_POOL_ID)
            send_mail(
                admin_users,
                f"PROMOTED: {user_email}",
                f"User {user_email} has been promoted to administrator by {actor}",
            )
        elif "close" in event["path"]:
            body = close_handler(user_name, client, USER_POOL_ID)
            send_mail(
                admin_users,
                f"CLOSED: {user_email}",
                f"User {user_email} account closed by user request",
            )
        elif "deny" in event["path"]:
            body = deny_handler(user_name, client, USER_POOL_ID)
            send_mail(
                admin_users,
                f"DENIED: {user_email}",
                f"User {user_email} has been denied by {actor}",
            )
        elif "delete" in event["path"]:
            body = delete_handler(user_name, client, USER_POOL_ID)
            send_mail(
                admin_users,
                f"DELETED: {user_email}",
                f"User {user_email} has been deleted by {actor}",
            )
        else:
            raise RuntimeError("Invalid POST path: " + event["path"])
    else:
        raise RuntimeError("Invalid http method: " + event["httpMethod"])

    return {
        "statusCode": HTTP_OK,
        "headers": {
            "Access-Control-Allow-Origin": CORS_ORIGIN_ALL,
            "Access-Control-Allow-Headers": CORS_HEADERS_ALL,
            "Access-Control-Allow-Methods": CORS_METHODS_GET_POST_OPTIONS,
        },
        "body": json.dumps(body),
    }
