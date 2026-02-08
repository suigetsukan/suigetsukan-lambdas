"""
The purpose of this lambda function is to take a file name, figure out what art and
technique it represents, and then stuff the file's url into the appropriate DynamoDB
table.
"""
#  Copyright (c) 2023. Suigetsukan Dojo

import json

import aikido
import battodo
import danzan_ryu
import utils


def extract_file_url(event):
    """
    Extract the file URL from the event

    :param event: The Lambda event
    :return: The file URL
    """
    subject = event["Records"][0]["Sns"]["Subject"]
    if "Complete" in subject:
        print("'Complete' type notification detected")
        file_url = json.loads(event["Records"][0]["Sns"]["Message"])["hlsUrl"]
    elif "Direct" in subject:
        print("'Direct' type notification detected")
        file_url = event["Records"][0]["Sns"]["Message"]
    elif "Ingest" in subject:
        print("'Ingest' type notification detected. No further processing.")
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
    print(json.dumps(event))  # for debugging purposes & use for test data
    file_url = extract_file_url(event)
    if file_url:
        print("hls url: " + file_url)
        file_stem = utils.get_stub(file_url)
        print("file stem: " + file_stem)
        if file_stem.startswith("d"):
            print("art: danzan ryu")
            danzan_ryu.handle_danzan_ryu(file_url)
        elif file_stem.startswith("b"):
            print("art: battodo")
            battodo.handle_battodo(file_url)
        elif file_stem.startswith("a"):
            print("art: aikido")
            aikido.handle_aikido(file_url)
        else:
            raise RuntimeError("Invalid video file name: " + file_stem)
    return
