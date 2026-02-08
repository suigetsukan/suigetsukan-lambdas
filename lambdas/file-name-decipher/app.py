"""
The purpose of this lambda function is to take a file name, figure out what art and
technique it represents, and then stuff the file's url into the appropriate DynamoDB
table.
"""
#  Copyright (c) 2023. Suigetsukan Dojo

import json
import logging

import aikido
import battodo
import danzan_ryu
import utils

logger = logging.getLogger(__name__)


def extract_file_url(event):
    """
    Extract the file URL from the event

    :param event: The Lambda event
    :return: The file URL
    """
    if not event.get("Records") or not isinstance(event["Records"], list):
        raise ValueError("Invalid event: missing or empty Records")
    record = event["Records"][0]
    sns = record.get("Sns") if isinstance(record, dict) else None
    if not sns or "Subject" not in sns:
        raise ValueError("Invalid event: missing Sns or Subject")
    subject = sns["Subject"]
    if "Complete" in subject:
        logger.debug("Complete type notification detected")
        try:
            msg = json.loads(sns["Message"])
        except json.JSONDecodeError as err:
            raise ValueError("Invalid JSON in Complete notification Message") from err
        file_url = msg.get("hlsUrl")
        if not file_url:
            raise ValueError("Complete notification missing hlsUrl")
    elif "Direct" in subject:
        logger.debug("Direct type notification detected")
        file_url = sns.get("Message") or ""
        if not file_url or not isinstance(file_url, str):
            raise ValueError("Direct notification missing or invalid Message")
    elif "Ingest" in subject:
        logger.debug("Ingest type notification detected, no further processing")
        file_url = None
    else:
        raise RuntimeError("Unknown notification type: " + subject)

    return file_url


def lambda_handler(event, context):
    """
    Lambda function handler

    :param event: The Lambda event
    :param context: The Lambda context
    :return: Nothing
    """
    logger.debug("Processing SNS notification")
    file_url = extract_file_url(event)
    if file_url:
        file_stem = utils.get_stub(file_url)
        logger.info("Processing file stem: %s", file_stem)
        if file_stem.startswith("d"):
            danzan_ryu.handle_danzan_ryu(file_url)
        elif file_stem and file_stem[0] in "bcefghijklm":
            battodo.handle_battodo(file_url)
        elif file_stem.startswith("a"):
            aikido.handle_aikido(file_url)
        else:
            raise RuntimeError("Invalid video file name: " + file_stem)
    return
