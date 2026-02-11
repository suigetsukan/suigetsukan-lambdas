"""
This handles the Aikido portion of the suigetsukan-curriculum website.
"""
#  Copyright (c) 2023. Suigetsukan Dojo

import logging
import os
import re

import boto3
from boto3.dynamodb.conditions import Key

import utils
from common.aikido_mappings import AIKIDO_REGEX_LOOKUP, AIKIDO_SCROLL_LOOKUP
from common.constants import DDB_INDEX_NAME, DDB_ITEMS_KEY, DDB_MAP_KEY, DDB_VARIATIONS_KEY, HTTP_OK

logger = logging.getLogger(__name__)
DDB_AIKIDO_TABLE = os.environ["AWS_DDB_AIKIDO_TABLE_NAME"]


def locate_technique_in_json(scroll, file_stem, json_data):
    """
    Find the index (offset) of the technique in the scroll's json_data from the file_stem.
    """
    offset = None
    pattern = AIKIDO_REGEX_LOOKUP[scroll]
    parts = re.findall(pattern, file_stem)
    parts_length = len(parts)

    if not parts_length:
        raise RuntimeError("Could not find aikido pattern for " + scroll + " in " + file_stem)

    if parts_length == 1:
        technique_number = str(int(parts[0]))
        offset = utils.find_one_datapoint_item_offset("Number", technique_number, json_data)
    else:
        raise RuntimeError(scroll + " not yet implemented")

    return offset


def update_ddb(scroll, file_stem, ddb_table, hls_url):
    """
    Update the ddb table with the hls url
    :param scroll: The scroll label
    :param file_stem: The file stem that tells us how to find the offset
    :param ddb_table: The ddb table to update
    :param hls_url: The hls url
    :return: The updated table response
    """
    query_response = ddb_table.query(
        IndexName=DDB_INDEX_NAME, KeyConditionExpression=Key("Name").eq(scroll)
    )
    if query_response["ResponseMetadata"]["HTTPStatusCode"] != HTTP_OK:
        raise RuntimeError("Database query failed")
    if query_response["Count"] == 0:
        raise RuntimeError("Scroll not found in database")
    json_data = query_response["Items"][0][DDB_MAP_KEY][DDB_ITEMS_KEY]
    data_offset = locate_technique_in_json(scroll, file_stem, json_data)
    current_variations = query_response["Items"][0][DDB_MAP_KEY][DDB_ITEMS_KEY][data_offset][
        DDB_VARIATIONS_KEY
    ]
    new_variations = utils.handle_variations(current_variations, hls_url)
    query_response["Items"][0][DDB_MAP_KEY][DDB_ITEMS_KEY][data_offset].update(
        {DDB_VARIATIONS_KEY: new_variations}
    )
    return ddb_table.put_item(Item=query_response["Items"][0])


def handle_aikido(hls_url):
    """
    Handle the aikido list

    :param file_stem: The file fragment that dictates how the db is updated
    :param file_url: The file URL to add to the list of variations in the data dictionary
    :return: Nothing
    """
    my_ddb_table = boto3.resource("dynamodb").Table(DDB_AIKIDO_TABLE)
    stub = utils.get_stub(hls_url)
    parts = re.findall(r"^a([0-9]{2}).*$", stub)
    if not parts:
        raise RuntimeError(f"Invalid aikido stub pattern: {stub}")
    scroll_name = AIKIDO_SCROLL_LOOKUP[int(parts[0])]  # use int to remove leading zeros
    logger.debug("Processing scroll: %s", scroll_name)
    response = update_ddb(scroll_name, stub, my_ddb_table, hls_url)
    if response["ResponseMetadata"]["HTTPStatusCode"] != HTTP_OK:
        raise RuntimeError("Failed to update the database")
