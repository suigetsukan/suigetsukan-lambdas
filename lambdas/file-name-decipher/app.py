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


def _extract_url_from_complete(sns):
    """Extract hlsUrl from a Complete-type SNS message."""
    logger.debug("Complete type notification detected")
    try:
        msg = json.loads(sns["Message"])
    except json.JSONDecodeError as err:
        raise ValueError("Invalid JSON in Complete notification Message") from err
    file_url = msg.get("hlsUrl")
    if not file_url:
        raise ValueError("Complete notification missing hlsUrl")
    return file_url


def _extract_url_from_direct(sns):
    """Extract file URL from a Direct-type SNS message."""
    logger.debug("Direct type notification detected")
    file_url = sns.get("Message") or ""
    if not file_url or not isinstance(file_url, str):
        raise ValueError("Direct notification missing or invalid Message")
    return file_url


def extract_file_url(event):
    """
    Extract the file URL from the event

    :param event: The Lambda event
    :return: The file URL or None for Ingest (no processing).
    """
    if not event.get("Records") or not isinstance(event["Records"], list):
        raise ValueError("Invalid event: missing or empty Records")
    record = event["Records"][0]
    sns = record.get("Sns") if isinstance(record, dict) else None
    if not sns or "Subject" not in sns:
        raise ValueError("Invalid event: missing Sns or Subject")
    subject = sns["Subject"]
    if "Complete" in subject:
        return _extract_url_from_complete(sns)
    if "Direct" in subject:
        return _extract_url_from_direct(sns)
    if "Ingest" in subject:
        logger.debug("Ingest type notification detected, no further processing")
        return None
    raise RuntimeError("Unknown notification type: " + subject)


def lambda_handler(event, _context):
    """
    Lambda function handler

    :param event: The Lambda event
    :param _context: The Lambda context (unused)
    :return: None
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
