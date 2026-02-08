"""
This is part of a lambda function which is used to create an entry in the DynamoDB table based
on an url
"""

import os
import re
from pathlib import Path
from urllib.parse import urlparse

import boto3
from boto3.dynamodb.conditions import Key

import utils
from common.constants import DDB_INDEX_NAME, DDB_ITEMS_KEY, DDB_MAP_KEY, DDB_VARIATIONS_KEY, HTTP_OK
from common.danzan_ryu_mappings import (
    DANZAN_RYU_DRILL_GROUPS,
    DANZAN_RYU_GOSHIN_DICT,
    DANZAN_RYU_KDM_DICT,
    DANZAN_RYU_SCROLL_DICT,
    DANZAN_RYU_WEAPON_DICT,
)

DDB_DANZAN_RYU_TABLE = os.environ["AWS_DDB_DANZAN_RYU_TABLE_NAME"]


def get_danzan_ryu_scroll_name(scroll_char):
    """
    Identify the scroll we are processing by using a single character
    which was embedded in the file name

    :param scroll_char: A single character from the file name
    :return: the scroll name
    """
    return DANZAN_RYU_SCROLL_DICT[scroll_char]


def get_weapon_id(weapon_char):
    """
    Identify the weapon we are processing by using a single character
    which was embedded in the file name

    :param weapon_char: A single character from the file name
    :return: the weapon name
    """
    return DANZAN_RYU_WEAPON_DICT[weapon_char]


def get_kdm_id(kdm_char):
    """
    Identify the KDM we are processing by using a single character
    which was embedded in the file name

    :param kdm_char: A single character from the file name
    :return: the KDM name
    """
    return DANZAN_RYU_KDM_DICT[kdm_char]


def get_goshin_id(goshin_char):
    """
    Identify the goshin we are processing by using a single character
    which was embedded in the file name

    :param goshin_char: A single character from the file name
    :return: the goshin name
    """
    return DANZAN_RYU_GOSHIN_DICT[goshin_char]


def get_drill_group_name(group_number):
    """
    Get the drill group name

    :param group_number: The drill group number
    :return: The drill group name
    """
    return DANZAN_RYU_DRILL_GROUPS[group_number]


def get_stub(file_url):
    """
    Get the stub from a file name

    :param file_url: The file name
    :return: The stub in lower case
    """
    file_name = urlparse(file_url).path.split("/")[-1]
    # print(file_name)
    return Path(str(file_name).lower()).stem


def sort_url_by_stub(url_list):
    """
    Sort the list of urls by the stub

    :param url_list: The list of urls
    :return: The sorted list of urls
    """
    url_list.sort(key=get_stub)
    return url_list


def handle_basic_weapons(file_stem, json_data):
    """
    Find the offset of the first item in the json data that matches the label and value in the
    basic weapons list

    :param file_stem: The file stem that tells us the set and technique number
    :param json_data: The data we need to find the offset for
    :return: The offset of the item
    """
    item_stem = utils.remove_char(file_stem, 0)
    set_number = item_stem[0]
    technique_number = item_stem[1]
    return utils.find_two_datapoints_item_offset(
        "Set", set_number, "Number", technique_number, json_data
    )


def handle_advanced_weapons(file_stem, json_data):
    """
    Find the offset of the first item in the json data that matches the label and value in the
    advanced weapons list

    :param file_stem: The file stem that tells us the set and technique number
    :param json_data: The data we need to find the offset for
    :return: The offset of the item
    """
    weapon = get_weapon_id(file_stem[1])
    number = file_stem[2]
    return utils.find_two_datapoints_item_offset("Weapon", weapon, "Number", number, json_data)


def handle_kdm(file_stem, json_data):
    """
    Handle the offset of the first item in the json data that matches the label and value in the
    KDM list
    :param file_stem: The file stem that tells us the drill type and number
    :param json_data: The data we need to find the offset for
    :return: The offset of the item
    """
    drill_type = get_kdm_id(file_stem[1])
    number = file_stem[2]
    return utils.find_two_datapoints_item_offset(
        "DrillType", drill_type, "Number", number, json_data
    )


def handle_shime(file_stem, json_data):
    """
    Handle the offset of the first item in the json data that matches the label and value in the
    shime list
    :param file_stem: The file stem that tells us the flow number and technique number
    :param json_data: The data we need to find the offset for
    :return: The offset of the item
    """
    flow_number = file_stem[1]
    technique_number = file_stem[2]
    return utils.find_two_datapoints_item_offset(
        "GroundFlowNumber", flow_number, "Number", technique_number, json_data
    )


def handle_goshin(file_stem, json_data):
    """
    Handle the offset of the first item in the json data that matches the label and value in the
    goshin list
    :param file_stem: The file stem that tells us the goshin number and technique number
    :param json_data: The data we need to find the offset for
    :return: The offset of the item
    """
    entering_direction = get_goshin_id(file_stem[1])
    number = file_stem[2]
    return utils.find_two_datapoints_item_offset(
        "Enter", entering_direction, "Number", number, json_data
    )


def handle_daito_no_maki(file_stem, json_data):
    """
    Handle the offset of the first item in the json data that matches the label and value in the
    daito no maki list
    :param file_stem: The file stem that tells us the daito number and technique number
    :param json_data: The data we need to find the offset for
    :return: The offset of the item
    """
    group = file_stem[1]
    temp = re.findall(r"\d+", file_stem)
    number = temp[0]
    return utils.find_two_datapoints_item_offset("Group", group, "Number", number, json_data)


def handle_shime_groundflow(file_stem, json_data):
    """
    Handle the offset of the first item in the json data that matches the label and value in the
    shime groundflow list
    :param file_stem: The file stem that tells us the groundflow number and technique number
    :param json_data: The data we need to find the offset for
    :return: The offset of the item
    """
    flow_number = file_stem[1]
    # print(flow_number)
    return utils.find_one_datapoint_item_offset("Number", flow_number, json_data)


def handle_katsu_kappo(file_stem, json_data):
    """
    Handle the offset of the first item in the json data that matches the label and value in the
    katsu kappo list
    :param file_stem: The file stem that tells us the katsu number and technique number
    :param json_data: The data we need to find the offset for
    :return: The offset of the item
    """
    temp = re.findall(r"(\d+)", file_stem)
    section = str(temp[0])[0:2]
    number = str(temp[0])[-1]
    return utils.find_two_datapoints_item_offset("Section", section, "Number", number, json_data)


def handle_drills(file_stem, json_data):
    """
    Handle the offset of the first item in the json data that matches the label and value in the
    drills list
    :param file_stem:  The file stem that tells us the group number and technique number
    :param json_data: The data we need to find the offset for
    :return: The offset of the item
    """
    names = re.findall(r"^p([0-9]{2})([0-9]{2})([0-9]{2})([a-y]{1})$", file_stem)
    group_name = get_drill_group_name(names[0][0])
    set_number = str(int(names[0][1]))  # int() removes the leading zero
    technique_number = str(int(names[0][2]))  # int() removes the leading zero
    return utils.find_three_datapoints_item_offset(
        "Group", group_name, "Set", set_number, "Number", technique_number, json_data
    )


def handle_simple_table_model(file_stem, json_data):
    """
    Handle the offset of the first item in the json data that matches the label and value in the
    simple table model list
    :param file_stem: The file stem that tells us the table number
    :param json_data: The data we need to find the offset for
    :return: The offset of the item
    """
    item_stem = utils.remove_char(file_stem, 0)
    temp = re.findall(r"\d+", item_stem)
    table_item_number = temp[0]
    return utils.find_one_datapoint_item_offset("Number", table_item_number, json_data)


def pick_danzan_ryu_scroll_handler(scroll, file_stem, json_data):
    """
    Pick a scroll handler based on the scroll label
    :param scroll: The scroll label
    :param file_stem: The file stem that tells us how to find the offset
    :param json_data: The data we need to find the offset for
    :return: The offset of the item
    """

    if scroll in ("basic_stick", "basic_knife", "basic_handgun"):
        data_offset = handle_basic_weapons(file_stem, json_data)
    elif scroll == "advanced_weapons":
        data_offset = handle_advanced_weapons(file_stem, json_data)
    elif scroll == "kdm":
        data_offset = handle_kdm(file_stem, json_data)
    elif scroll == "shime":
        data_offset = handle_shime(file_stem, json_data)
    elif scroll == "goshin":
        data_offset = handle_goshin(file_stem, json_data)
    elif scroll == "daito_no_maki":
        data_offset = handle_daito_no_maki(file_stem, json_data)
    elif scroll == "shime_groundflow":
        data_offset = handle_shime_groundflow(file_stem, json_data)
    elif scroll == "katsu_kappo":
        data_offset = handle_katsu_kappo(file_stem, json_data)
    elif scroll == "drills":
        data_offset = handle_drills(file_stem, json_data)
    else:
        data_offset = handle_simple_table_model(file_stem, json_data)
    return data_offset


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
        raise RuntimeError("Scroll not found")
    json_data = query_response["Items"][0][DDB_MAP_KEY][DDB_ITEMS_KEY]
    data_offset = pick_danzan_ryu_scroll_handler(scroll, file_stem, json_data)
    current_variations = query_response["Items"][0][DDB_MAP_KEY][DDB_ITEMS_KEY][data_offset][
        DDB_VARIATIONS_KEY
    ]
    new_variations = utils.handle_variations(current_variations, hls_url)
    query_response["Items"][0][DDB_MAP_KEY][DDB_ITEMS_KEY][data_offset].update(
        {DDB_VARIATIONS_KEY: new_variations}
    )
    return ddb_table.put_item(Item=query_response["Items"][0])


def handle_danzan_ryu(hls_url):
    """
    Handle the danzan ryu hls url set
    :param hls_url: The hls url
    :return: The updated hls url in the database
    """
    my_ddb_table = boto3.resource("dynamodb").Table(DDB_DANZAN_RYU_TABLE)
    stub = utils.get_file_stub(hls_url)
    stu = utils.remove_char(stub, 0)
    scroll_name = get_danzan_ryu_scroll_name(stu[0])
    print(scroll_name)
    response = update_ddb(scroll_name, stu, my_ddb_table, hls_url)
    if response["ResponseMetadata"]["HTTPStatusCode"] != HTTP_OK:
        raise RuntimeError("Failed to update the database")
