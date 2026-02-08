"""
This module contains utility functions for the AWS Lambda function
"""
#  Copyright (c) 2023. Suigetsukan Dojo

from pathlib import Path
from urllib.parse import urlparse

import boto3

from common.constants import DDB_VARIATIONS_KEY, REMOVE_ALL_TECHNIQUE_VARIATIONS


def find_one_datapoint_item_offset(label1, value1, json_data):
    """
    Find the offset of the first item in the json data that matches the label and value

    :param label1: The label of the item to find
    :param value1: The value of the item to find
    :param json_data: The json data
    :return: The offset of the item
    """
    for offset, item in enumerate(json_data):
        if item[label1] == value1:
            return offset
    raise RuntimeError(f"Could not find valid offset values for label/value: {label1}/{value1}")


def find_two_datapoints_item_offset(label1, value1, label2, value2, json_data):
    """
    Find the offset of the first item in the json data that matches the labels and values

    :param label1: The label of the first item to find
    :param value1: The value of the first item to find
    :param label2: The label of the second item to find
    :param value2: The value of the second item to find
    :param json_data: The json data
    :return: The offset of the item
    """
    for offset, item in enumerate(json_data):
        if item[label1] == value1 and item[label2] == value2:
            return offset
    raise RuntimeError("Could not find valid offset values for 2 labels/values")


def find_three_datapoints_item_offset(label1, value1, label2, value2, label3, value3, json_data):
    """
    Find the offset of the first item in the json data that matches the labels and values

    :param label1: The label of the first item to find
    :param value1: The value of the first item to find
    :param label2: The label of the second item to find
    :param value2: The value of the second item to find
    :param label3: The label of the third item to find
    :param value3: The value of the third item to find
    :param json_data: The json data
    :return: The offset of the item
    """
    for offset, item in enumerate(json_data):
        if item[label1] == value1 and item[label2] == value2 and item[label3] == value3:
            return offset
    raise RuntimeError("Could not find valid offset values for 3 labels/values")


def get_hls_url_stub_letter(url):
    """
    Get the letter from the hls url stub

    :param url: The hls url
    :return: The letter
    """
    return url.split("/")[-1].split(".")[0][-1]


def handle_variations(current_variations, hls_url):
    """
    Create or update the list of variations for a given hls url

    :param current_variations: A list of variations (urls)
    :param hls_url: The new hls url
    :return: A sorted list of variations (urls)
    """
    new_variations = []
    if not current_variations:
        new_variations.append(hls_url)
    else:
        variation_set = set()
        variation_set.add(get_hls_url_stub_letter(hls_url))
        new_variations.append(hls_url)

        for url in current_variations:
            # print('url = ', url)
            stub_letter = get_hls_url_stub_letter(url)
            # print('stub_letter = ', stub_letter)
            if stub_letter not in variation_set:
                variation_set.add(stub_letter)
                new_variations.append(url)

    return sort_url_by_stub(new_variations)


def get_file_stub(hls_url):
    """
    Get the file stub from the hls url
    :param hls_url: The hls url
    :return: The file stub
    """
    stub = hls_url.split("/")[-1]
    return stub.split(".")[0]


def remove_char(a_str, n):
    """
    Remove a character from a string

    :param a_str: The string to remove the character from
    :param n: The index of the character to remove
    :return: The string with the character removed
    """
    first_part = a_str[:n]
    last_part = a_str[n + 1 :]
    return first_part + last_part


def convert_to_camel_case(string):
    """
    Convert a string to camel case

    :param string: The string to convert
    :return: The string in camel case
    """
    return "".join(x.capitalize() or "_" for x in string.split("_"))


def get_stub(file_url):
    """
    Get the stub from a file name

    :param file_url: The file name
    :return: The stub in lower case
    """
    file_name = urlparse(file_url).path.split("/")[-1]
    print(file_name)
    return Path(str(file_name).lower()).stem


def sort_url_by_stub(url_list):
    """
    Sort the list of urls by the stub

    :param url_list: The list of urls
    :return: The sorted list of urls
    """
    url_list.sort(key=get_stub)
    return url_list


def get_full_ddb_table_name(table_name_string):
    """
    Find the DynamoDB table based a string version of the table name
    :param table_name_string: A string of the partial table name
    :return: The full ddb table name
    """
    ddb_client = boto3.client("dynamodb")
    response = ddb_client.list_tables(ExclusiveStartTableName=table_name_string, Limit=1)
    return response["TableNames"][0]


def extract_stub_list(variations):
    """
    Extract the list of file stubs from the list of urls in variations list

    :param variations: The variations
    :return: The list of stubs
    """
    stub_list = []
    for variation in variations:
        print(variation)
        stub = get_stub(variation)
        stub_list.append(stub)
    return stub_list


def update_technique_list(data, table, file_url):
    """
    Update the technique list in the DynamoDB table

    :param data: The data dictionary
    :param table: The DynamoDB table
    :param file_url: The file URL to add to the list of variations in the data dictionary.
    :return: Nothing
    """

    # data = update_variations(data, file_url)
    new_stub = get_stub(file_url)
    stub_list = extract_stub_list(data[DDB_VARIATIONS_KEY])
    if new_stub in stub_list:
        print(f"Updating existing entry: {new_stub}")
        for i, stub in enumerate(stub_list):
            if stub == new_stub:
                data[DDB_VARIATIONS_KEY][i] = file_url
    else:
        print(f"Adding new entry: {new_stub}")
        data[DDB_VARIATIONS_KEY].append(file_url)

    data[DDB_VARIATIONS_KEY] = sort_url_by_stub(data[DDB_VARIATIONS_KEY])
    put_response = table.put_item(Item=data)
    print(put_response)


def reset_technique_list(data, table):
    """
    Special case. Remove the technique list in the DynamoDB table.
    Used for testing purposes.

    :param data: The data dictionary
    :param table: The DynamoDB table
    :return: Nothing
    """
    data[DDB_VARIATIONS_KEY] = []
    put_response = table.put_item(Item=data)
    print(put_response)


def handle_technique_list(file_stem, data, table_handle, file_url):
    """
    Handle the technique list.

    :param file_stem: A partial file name which dictates how the db is updated
    :param data: a DynamoDB data dictionary
    :param table_handle: A handle to the DynamoDB table
    :param file_url: A URL to add to the list of variations in the data dictionary
    :return: Nothing
    """
    if file_stem.endswith(REMOVE_ALL_TECHNIQUE_VARIATIONS):
        reset_technique_list(data, table_handle)
    else:
        update_technique_list(data, table_handle, file_url)


def get_table_handle(partial_table_name):
    """
    Get a handle to the DynamoDB table.
    :param partial_table_name: The partial DynamoDB table name
    :return: The DynamoDB table handle
    """
    table_name = get_full_ddb_table_name(partial_table_name)
    ddb_client = boto3.resource("dynamodb")
    table_handle = ddb_client.Table(table_name)
    return table_handle
