"""
This file handles the Battodo (sword) portion of the suigetsukan-curriculum utility
"""
#  Copyright (c) 2023. Suigetsukan Dojo

import logging
import os
import re

import boto3
from boto3.dynamodb.conditions import Key

import utils
from common.battodo_mappings import (
    BATTODO_SCROLL_DICT,
    KATA_BATTOHO_LEVEL,
    KATA_NAME,
    KATA_TECHNIQUE,
    KATA_TOYAMA_RYU_LEVEL,
    KUMITACHI_LEVEL,
    KUMITACHI_NIDAN_NO_WAZA_TECHNIQUE,
    KUMITACHI_NIDAN_NO_WAZA_TSUKI_TECHNIQUE,
    KUMITACHI_RANDORI_OKUDEN_TECHNIQUE,
    KUMITACHI_SANDAN_NO_WAZA_TECHNIQUE,
    KUMITACHI_SHODAN_NO_WAZA_DEFENSE,
    SUBURI_SANDAN_SABAKI_CUT_TYPE,
    SUBURI_SANDAN_SABAKI_FOOTWORK_TYPE,
    TAMESHIGIRI_RANK,
    TAMESHIGIRI_TECHNIQUE,
)
from common.constants import DDB_INDEX_NAME, DDB_ITEMS_KEY, DDB_MAP_KEY, DDB_VARIATIONS_KEY, HTTP_OK

logger = logging.getLogger(__name__)
DDB_BATTODO_TABLE = os.environ["AWS_DDB_BATTODO_TABLE_NAME"]


def _require_parts(parts, file_stem, scroll):
    """Raise if parts is empty (regex did not match)."""
    if not parts:
        raise RuntimeError(f"Invalid Battodo file stem for {scroll}: {file_stem}")


def get_battodo_scroll_name(scroll_char):
    """
    Identify the scroll we are processing by using a single character
    which was embedded in the file name

    :param scroll_char: A single character from the file name
    :return: the scroll name
    """
    return BATTODO_SCROLL_DICT[scroll_char]


def handle_suburi_shodan_uchi_waza(file_stem, json_data):
    """
    Handle the shodan_uchi_waza scroll

    :param file_stem: The stem of the file name
    :param json_data: The json data from the file
    :return: Offset in the json data
    """
    parts = re.findall(r"^c([0-9]{2})([a-y]{1})$", file_stem)
    _require_parts(parts, file_stem, "shodan_uchi_waza")
    section_number = str(int(parts[0][0]))  # remove the leading 0
    return utils.find_one_datapoint_item_offset("Number", section_number, json_data)


def handle_suburi_sandan_uchi_waza(file_stem, json_data):
    """
    Handl the suburi sandan_uchi_waza ddb entry

    :param file_stem: The stem of the file name indication how to handle the hls url
    :param json_data: The json data taken from the ddb table
    :return: The offset into the json data
    """
    parts = re.findall(r"^f([0-9]{2})([a-y]{1})$", file_stem)
    _require_parts(parts, file_stem, "sandan_uchi_waza")
    section_number = str(int(parts[0][0]))  # remove the leading 0
    return utils.find_one_datapoint_item_offset("Number", section_number, json_data)


def lookup_suburi_sandan_sabaki_cut_type(cut_type):
    """
    Lookup the cut_type for suburi

    :param cut_type: The one-character cut_type indicator
    :return: The full cut type name
    """
    return SUBURI_SANDAN_SABAKI_CUT_TYPE[cut_type]


def lookup_suburi_sandan_sabaki_footwork_type(footwork_type):
    """
    Look up the footwork_type for suburi

    :param footwork_type: The one-character footwork_type indicator
    :return: The full footwork type name
    """
    return SUBURI_SANDAN_SABAKI_FOOTWORK_TYPE[footwork_type]


def handle_suburi_sandan_sabaki(file_stem, json_data):
    """
    Handle the suburi sandan_sabaki scroll
    :param file_stem: The stem of the file indicating how to handle the hls url
    :param json_data: The json data taken from the ddb table
    :return: The offset into the json data
    """
    parts = re.findall(r"^g([a-z]{1})([a-z]{1})([a-y]{1})$", file_stem)
    _require_parts(parts, file_stem, "sandan_sabaki")
    cut_type = lookup_suburi_sandan_sabaki_cut_type(parts[0][0])
    footwork_type = lookup_suburi_sandan_sabaki_footwork_type(parts[0][1])
    return utils.find_two_datapoints_item_offset(
        "Name", cut_type, "Footwork", footwork_type, json_data
    )


def handle_suburi_sayu_giri(file_stem, json_data):
    """
    Handle the suburi sayu_giri scroll

    :param file_stem: The stem of the file indicating how to handle the hls url
    :param json_data: The json data taken from the ddb table
    :return: The offset into the json data
    """
    parts = re.findall(r"^e([0-9]{2})([a-y]{1})$", file_stem)
    _require_parts(parts, file_stem, "sayu_giri")
    section_number = str(int(parts[0][0]))  # remove the leading 0
    return utils.find_one_datapoint_item_offset("Number", section_number, json_data)


def lookup_kumitachi_level(level):
    """
    Lookup the kumitachi level

    :param level: The one-character level indicator
    :return: The full kumitachi level name
    """
    return KUMITACHI_LEVEL[level]


def lookup_kumitachi_shodan_no_waza_defense(defense):
    """
    Lookup the kumitachi defense techniques

    :param defense: The one-character defense indicator
    :return: The full kumitachi defense name
    """
    return KUMITACHI_SHODAN_NO_WAZA_DEFENSE[defense]


def handle_kumitachi_shodan_no_waza(file_stem, json_data):
    """
    Handle the kumitachi shodan_no_waza scroll
    :param file_stem: The stem of the file indicating how to handle the hls url
    :param json_data: The json data taken from the ddb table
    :return: The offset into the json data
    """
    parts = re.findall(r"^d([0-9]{2})([a-z])[a-y]$", file_stem)
    _require_parts(parts, file_stem, "shodan_no_waza")
    set_number = str(int(parts[0][0]))  # remove the leading 0
    defense = lookup_kumitachi_shodan_no_waza_defense(parts[0][1])
    return utils.find_two_datapoints_item_offset("Set", set_number, "Name", defense, json_data)


def handle_kumitachi_sandan_no_waza_set_one(file_stem, json_data):
    """
    The kumitachi sandan_no_waza scroll
    :param file_stem: The stem of the file indicating how to handle the hls url
    :param json_data: The json data taken from the ddb table
    :return: The offset into the json data
    """
    parts = re.findall(r"^h([0-9]{2}).*$", file_stem)
    _require_parts(parts, file_stem, "sandan_no_waza_set_one")
    set_number = str(int(parts[0]))  # remove the leading 0
    return utils.find_one_datapoint_item_offset("Number", set_number, json_data)


def lookup_kumitachi_sandan_no_waza_technique(technique_letter):
    """
    Lookup the kumitachi sandan_no_waza technique

    :param technique_letter: The one-character technique indicator
    :return: The full kumitachi technique name
    """
    return KUMITACHI_SANDAN_NO_WAZA_TECHNIQUE[technique_letter]


def handle_kumitachi_sandan_no_waza_set_two(file_stem, json_data):
    """
    Handle kumitachi sandan_no_waza scroll  (set 2)
    :param file_stem: The stem of the file indicating how to handle the hls url
    :param json_data: The json data taken from the ddb table
    :return: The offset into the json data
    """
    parts = re.findall(r"^h([0-9]{2})([a-z]{1})([a-z]{1})[a-z]{1}$", file_stem)
    _require_parts(parts, file_stem, "sandan_no_waza_set_two")
    set_number = str(int(parts[0][0]))  # remove the leading 0
    technique = lookup_kumitachi_sandan_no_waza_technique(parts[0][1])
    level = lookup_kumitachi_level(parts[0][2])
    return utils.find_three_datapoints_item_offset(
        "Set", set_number, "Level", level, "Name", technique, json_data
    )


def handle_kumitachi_sandan_no_waza(file_stem, json_data):
    """
    Handle the kumitachi sandan_no_waza scroll

    :param file_stem: The stem of the file indicating how to handle the hls url
    :param json_data: The json data taken from the ddb table
    :return: The offset into the json data
    """
    parts = re.findall(r"^h([0-9]{2}).*$", file_stem)
    _require_parts(parts, file_stem, "sandan_no_waza")
    set_number = parts[0]
    if set_number == "01":
        offset = handle_kumitachi_sandan_no_waza_set_one(file_stem, json_data)
    elif set_number == "02":
        offset = handle_kumitachi_sandan_no_waza_set_two(file_stem, json_data)
    else:
        raise RuntimeError(f"Kumitachi Sandan No Waza unknown set number: {set_number}")
    return offset


def lookup_kumitachi_randori_okuden_technique(technique_letter):
    """
    The kumitachi randori_okuden technique list lookup

    :param technique_letter: The one-character technique indicator
    :return: The full kumitachi technique name
    """
    return KUMITACHI_RANDORI_OKUDEN_TECHNIQUE[technique_letter]


def handle_kumitachi_randori_okuden(file_stem, json_data):
    """
    Handle kumitachi randori_okuden scroll

    :param file_stem: The stem of the file indicating how to handle the hls url
    :param json_data: The json data taken from the ddb table
    :return: The offset into the json data
    """
    parts = re.findall(r"^i([0-9]{2})([a-z]{1})[a-z]{1}$", file_stem)
    _require_parts(parts, file_stem, "randori_okuden")
    set_number = str(int(parts[0][0]))  # remove the leading 0
    technique = lookup_kumitachi_randori_okuden_technique(parts[0][1])
    return utils.find_two_datapoints_item_offset("Set", set_number, "Name", technique, json_data)


def lookup_kumitachi_nidan_no_waza_tsuki_technique(technique_letter):
    """
    Lookup the tsuki techniques

    :param technique_letter: The one-character technique indicator
    :return: The full tsuki kumitachi technique name
    """
    return KUMITACHI_NIDAN_NO_WAZA_TSUKI_TECHNIQUE[technique_letter]


def lookup_kumitachi_nidan_no_waza_technique(technique_letter):
    """
    Lookup the kumitachi nidan_no_waza technique names

    :param technique_letter: The one-character technique indicator
    :return: The full kumitachi technique name
    """
    return KUMITACHI_NIDAN_NO_WAZA_TECHNIQUE[technique_letter]


def handle_kumitachi_nidan_no_waza(file_stem, json_data):
    """
    Handle kumitachi nidan_no_waza scroll

    :param file_stem: The stem of the file indicating how to handle the hls url
    :param json_data: The json data taken from the ddb table
    :return: The offset into the json data
    """
    parts = re.findall(r"^j([0-9]{2})([a-z])([a-z])[a-y]$", file_stem)
    _require_parts(parts, file_stem, "nidan_no_waza")
    set_number = str(int(parts[0][0]))  # remove the leading 0
    technique_name = lookup_kumitachi_nidan_no_waza_technique(parts[0][1])
    if set_number == "1" and technique_name == "Tsuki":
        tsuki_level = lookup_kumitachi_nidan_no_waza_tsuki_technique(parts[0][2])
        offset = utils.find_three_datapoints_item_offset(
            "Set", set_number, "Level", tsuki_level, "Name", technique_name, json_data
        )
    else:
        level = lookup_kumitachi_level(parts[0][2])
        offset = utils.find_three_datapoints_item_offset(
            "Set", set_number, "Level", level, "Name", technique_name, json_data
        )
    return offset


def lookup_kata_name(kata_number):
    """
    Lookup kata from a number

    :param kata_number: The number of the kata
    :return: The full kata name
    """
    return KATA_NAME[kata_number]


def handle_kata(file_stem, json_data):
    """
    Handle the kata scroll

    :param file_stem: The stem of the file indicating how to handle the hls url
    :param json_data: The json data taken from the ddb table
    :return: The offset into the json data
    """
    parts = re.findall(r"^k([0-9]{2})[a-z]$", file_stem)
    _require_parts(parts, file_stem, "kata")
    kata = lookup_kata_name(parts[0])
    return utils.find_one_datapoint_item_offset("Name", kata, json_data)


def lookup_kata_technique(technique_number):
    """
    Lookup kata techniques based on a number
    :param technique_number: The technique number
    :return: The full kata technique name
    """
    return KATA_TECHNIQUE[technique_number]


def lookup_kata_battoho_level(level_number):
    """
    Lookup the kata level

    :param level_number: The level number
    :return: The full kata level name
    """
    return KATA_BATTOHO_LEVEL[level_number]


def handle_battoho(file_stem, json_data):
    """
    Handle the battoho scroll

    :param file_stem: The stem of the file indicating how to handle the hls url
    :param json_data: The json data taken from the ddb table
    :return: The offset into the json data
    """
    parts = re.findall(r"^l([0-9]{2})([0-9]{2})[a-z]$", file_stem)
    _require_parts(parts, file_stem, "battoho")
    technique = lookup_kata_technique(parts[0][0])
    level = lookup_kata_battoho_level(parts[0][1])
    return utils.find_two_datapoints_item_offset("Level", level, "Name", technique, json_data)


def lookup_kata_toyama_ryu_level(level_number):
    """
    Lookup the toyama ryu levels

    :param level_number: The level number
    :return: The full kata level name
    """
    return KATA_TOYAMA_RYU_LEVEL[level_number]


def handle_toyama_ryu(file_stem, json_data):
    """
    Handle the toyama ryu scroll

    :param file_stem: The stem of the file indicating how to handle the hls url
    :param json_data: The json data taken from the ddb table
    :return: The offset into the json data
    """
    parts = re.findall(r"^a([0-9]{2})([0-9]{2})[a-z]$", file_stem)
    _require_parts(parts, file_stem, "toyama_ryu")
    technique = lookup_kata_technique(parts[0][0])
    art = lookup_kata_toyama_ryu_level(parts[0][1])
    return utils.find_two_datapoints_item_offset("Art", art, "Name", technique, json_data)


def lookup_tameshigiri_rank(rank_number):
    """
    Lookup the tameshigiri ranks
    :param rank_number: The rank number
    :return: The full tameshigiri rank name
    """
    return TAMESHIGIRI_RANK[rank_number]


def lookup_tameshigiri_technique(rank, technique_number):
    """
    Lookup the tameshigiri techniques based on a rank and a technique number

    :param rank: The rank of the person doing tameshigiri
    :param technique_number: The technique number
    :return: The full tameshigiri technique name
    """
    counted_from_zero = int(technique_number) - 1
    return TAMESHIGIRI_TECHNIQUE[rank][counted_from_zero]


def handle_tameshigiri(file_stem, json_data):
    """
    Handle the tameshigiri scroll

    :param file_stem: The stem of the file indicating how to handle the hls url
    :param json_data: The json data taken from the ddb table
    :return: The offset into the json data
    """
    parts = re.findall(r"^b([0-9]{2})([0-9]{2})[a-z]$", file_stem)
    _require_parts(parts, file_stem, "tameshigiri")
    rank = lookup_tameshigiri_rank(parts[0][0])
    technique_number = int(parts[0][1])
    technique = lookup_tameshigiri_technique(rank, technique_number)
    return utils.find_two_datapoints_item_offset("Rank", rank, "Techniques", technique, json_data)


def handle_suburi_scroll(scroll, file_stem, json_data):
    """
    Handle the suburi scroll

    :param scroll: The scrolls in Suburi
    :param file_stem: The stem of the file indicating how to handle the hls url
    :param json_data: The json data taken from the ddb table
    :return: The offset into the json data
    """
    if scroll == "shodan_uchi_waza":
        offset = handle_suburi_shodan_uchi_waza(file_stem, json_data)
    elif scroll == "sandan_sabaki":
        offset = handle_suburi_sandan_sabaki(file_stem, json_data)
    elif scroll == "sayu_giri":
        offset = handle_suburi_sayu_giri(file_stem, json_data)
    elif scroll == "sandan_uchi_waza":
        offset = handle_suburi_sandan_uchi_waza(file_stem, json_data)
    else:
        raise RuntimeError(f"Invalid Battodo Suburi scroll: {scroll}")
    return offset


def handle_kumitachi_scroll(scroll, file_stem, json_data):
    """
    Handle the kumitachi scroll

    :param scroll: One of the kumitachi scrolls
    :param file_stem: The stem of the file indicating how to handle the hls url
    :param json_data: The json data taken from the ddb table
    :return: The offset into the json data
    """
    if scroll == "shodan_no_waza":
        offset = handle_kumitachi_shodan_no_waza(file_stem, json_data)
    elif scroll == "sandan_no_waza":
        offset = handle_kumitachi_sandan_no_waza(file_stem, json_data)
    elif scroll == "randori_okuden":
        offset = handle_kumitachi_randori_okuden(file_stem, json_data)
    elif scroll == "nidan_no_waza":
        offset = handle_kumitachi_nidan_no_waza(file_stem, json_data)
    else:
        raise RuntimeError(f"Invalid Battodo Kumitachi scroll: {scroll}")
    return offset


def handle_kata_scroll(scroll, file_stem, json_data):
    """
    The kata scroll handler

    :param scroll: The lists in the kata scroll
    :param file_stem: The stem of the file indicating how to handle the hls url
    :param json_data: The json data taken from the ddb table
    :return: The offset into the json data
    """
    if scroll == "kata":
        offset = handle_kata(file_stem, json_data)
    elif scroll == "battoho":
        offset = handle_battoho(file_stem, json_data)
    elif scroll == "toyama_ryu":
        offset = handle_toyama_ryu(file_stem, json_data)
    else:
        raise RuntimeError(f"Invalid Battodo Kata scroll: {scroll}")
    return offset


def handle_formalities(file_stem, json_data):
    """
    Handle the formalities scroll

    :param file_stem: The stem of the file indicating how to handle the hls url
    :param json_data: The json data taken from the ddb table
    :return: The offset into the json data
    """
    parts = re.findall(r"^m([0-9]{2})[a-z]$", file_stem)
    _require_parts(parts, file_stem, "formalities")
    technique_number = str(int(parts[0]))
    return utils.find_one_datapoint_item_offset("Number", technique_number, json_data)


def pick_battodo_scroll_handler(scroll, file_stem, json_data):
    """
    The main scroll handler

    :param scroll: The scroll name
    :param file_stem: The stem of the file indicating how to handle the hls url
    :param json_data: The json data taken from the ddb table
    :return: The offset into the json data
    """
    if scroll in ("shodan_uchi_waza", "sandan_sabaki", "sayu_giri", "sandan_uchi_waza"):
        offset = handle_suburi_scroll(scroll, file_stem, json_data)
    elif scroll in ("shodan_no_waza", "sandan_no_waza", "randori_okuden", "nidan_no_waza"):
        offset = handle_kumitachi_scroll(scroll, file_stem, json_data)
    elif scroll in ("kata", "battoho", "toyama_ryu"):
        offset = handle_kata_scroll(scroll, file_stem, json_data)
    elif scroll == "tameshigiri":
        offset = handle_tameshigiri(file_stem, json_data)
    elif scroll == "formalities":
        offset = handle_formalities(file_stem, json_data)
    else:
        raise RuntimeError(f"Invalid Battodo scroll: {scroll}")
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
        raise RuntimeError("Scroll not found")
    json_data = query_response["Items"][0][DDB_MAP_KEY][DDB_ITEMS_KEY]
    data_offset = pick_battodo_scroll_handler(scroll, file_stem, json_data)
    current_variations = query_response["Items"][0][DDB_MAP_KEY][DDB_ITEMS_KEY][data_offset][
        DDB_VARIATIONS_KEY
    ]
    new_variations = utils.handle_variations(current_variations, hls_url)
    query_response["Items"][0][DDB_MAP_KEY][DDB_ITEMS_KEY][data_offset].update(
        {DDB_VARIATIONS_KEY: new_variations}
    )
    return ddb_table.put_item(Item=query_response["Items"][0])


def handle_battodo(hls_url):
    """
    Handle the battodo techniques

    :param hls_url: The file URL to add to the list of variations in the data dictionary
    :return: Nothing
    """
    my_ddb_table = boto3.resource("dynamodb").Table(DDB_BATTODO_TABLE)
    stub = utils.get_stub(hls_url)
    if not stub:
        raise RuntimeError("Invalid battodo URL: no file stub")
    scroll_name = get_battodo_scroll_name(stub[0])
    logger.debug("Processing scroll: %s", scroll_name)
    response = update_ddb(scroll_name, stub, my_ddb_table, hls_url)
    if response["ResponseMetadata"]["HTTPStatusCode"] != HTTP_OK:
        raise RuntimeError("Failed to update the database")
