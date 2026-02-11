"""
This module contains utility functions for the AWS Lambda function
"""
#  Copyright (c) 2023. Suigetsukan Dojo

from urllib.parse import urlparse


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


def find_three_datapoints_item_offset(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    label1, value1, label2, value2, label3, value3, json_data
):
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
    stub = url.split("/")[-1].split(".")[0]
    if not stub:
        raise ValueError("URL has no file stub")
    return stub[-1]


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
            stub_letter = get_hls_url_stub_letter(url)
            if stub_letter not in variation_set:
                variation_set.add(stub_letter)
                new_variations.append(url)

    return sort_url_by_stub(new_variations)


def get_file_stub(hls_url):
    """
    Get the file stub from the hls url (lowercase, no extension).
    :param hls_url: The hls url
    :return: The file stub
    """
    return get_stub(hls_url)


def get_stub(file_url):
    """
    Get the stub from a file name or URL.

    :param file_url: The file name or URL
    :return: The stub in lower case (filename without extension)
    """
    file_name = urlparse(file_url).path.split("/")[-1]
    if not file_name:
        raise ValueError("URL has no file stub")
    return str(file_name).lower().rsplit(".", 1)[0] if "." in file_name else str(file_name).lower()


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


def sort_url_by_stub(url_list):
    """
    Sort the list of urls by the stub

    :param url_list: The list of urls
    :return: The sorted list of urls
    """
    url_list.sort(key=get_stub)
    return url_list
