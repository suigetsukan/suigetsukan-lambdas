"""
Tests for file-name-decipher utils module.
"""

import pytest

from utils import (
    convert_to_camel_case,
    find_one_datapoint_item_offset,
    find_three_datapoints_item_offset,
    find_two_datapoints_item_offset,
    get_file_stub,
    get_hls_url_stub_letter,
    get_stub,
    handle_variations,
    remove_char,
    sort_url_by_stub,
)


class TestFindOffsets:
    """Tests for find_*_datapoint_item_offset functions."""

    def test_find_one_datapoint_item_offset(self):
        json_data = [
            {"Number": "1", "Name": "First"},
            {"Number": "2", "Name": "Second"},
            {"Number": "5", "Name": "Fifth"},
        ]
        assert find_one_datapoint_item_offset("Number", "1", json_data) == 0
        assert find_one_datapoint_item_offset("Number", "5", json_data) == 2

    def test_find_one_datapoint_item_offset_raises_when_not_found(self):
        json_data = [{"Number": "1"}]
        with pytest.raises(RuntimeError, match="Could not find valid offset"):
            find_one_datapoint_item_offset("Number", "99", json_data)

    def test_find_two_datapoints_item_offset(self):
        json_data = [
            {"Set": "1", "Name": "A"},
            {"Set": "1", "Name": "B"},
            {"Set": "2", "Name": "A"},
        ]
        assert find_two_datapoints_item_offset("Set", "1", "Name", "B", json_data) == 1

    def test_find_two_datapoints_item_offset_raises_when_not_found(self):
        json_data = [{"Set": "1", "Name": "A"}]
        with pytest.raises(RuntimeError, match="Could not find valid offset"):
            find_two_datapoints_item_offset("Set", "1", "Name", "Z", json_data)

    def test_find_three_datapoints_item_offset(self):
        json_data = [
            {"Set": "1", "Level": "Kihon", "Name": "A"},
            {"Set": "1", "Level": "Kihon", "Name": "B"},
            {"Set": "1", "Level": "Jokyu", "Name": "A"},
        ]
        assert (
            find_three_datapoints_item_offset("Set", "1", "Level", "Jokyu", "Name", "A", json_data)
            == 2
        )

    def test_find_three_datapoints_item_offset_raises_when_not_found(self):
        json_data = [{"Set": "1", "Level": "Kihon", "Name": "A"}]
        with pytest.raises(RuntimeError, match="Could not find valid offset"):
            find_three_datapoints_item_offset("Set", "1", "Level", "X", "Name", "Y", json_data)


class TestHelpers:
    """Tests for utility helper functions."""

    def test_get_file_stub(self):
        assert get_file_stub("https://bucket.s3.amazonaws.com/path/a0101x.m3u8") == "a0101x"
        assert get_file_stub("/local/path/c01a.m3u8") == "c01a"

    def test_get_file_stub_empty_path_raises(self):
        with pytest.raises(ValueError, match="no file stub"):
            get_file_stub("https://cdn.example.com/")

    def test_get_hls_url_stub_letter(self):
        # Stub letter is the last character of the stem
        url = "https://bucket.s3.amazonaws.com/path/a0101x.m3u8"
        assert get_hls_url_stub_letter(url) == "x"

    def test_get_hls_url_stub_letter_empty_path_raises(self):
        with pytest.raises(ValueError, match="no file stub"):
            get_hls_url_stub_letter("https://cdn.example.com/")

    def test_remove_char(self):
        assert remove_char("abcde", 0) == "bcde"
        assert remove_char("abcde", 2) == "abde"
        assert remove_char("ab", 1) == "a"

    def test_handle_variations_empty(self):
        result = handle_variations([], "https://cdn.example.com/vid1.m3u8")
        assert result == ["https://cdn.example.com/vid1.m3u8"]

    def test_handle_variations_adds_new(self):
        current = ["https://cdn.example.com/vid_a.m3u8"]
        new_url = "https://cdn.example.com/vid_b.m3u8"
        result = handle_variations(current, new_url)
        assert len(result) == 2
        assert new_url in result

    def test_get_stub_from_path_only(self):
        assert get_stub("/local/path/c01a.m3u8") == "c01a"
        assert get_stub("filename.json") == "filename"

    def test_get_stub_empty_path_raises(self):
        with pytest.raises(ValueError, match="no file stub"):
            get_stub("https://cdn.example.com/")

    def test_get_stub_url_with_query_string(self):
        assert get_stub("https://bucket.s3.amazonaws.com/path/a0101x.m3u8?version=1") == "a0101x"

    def test_convert_to_camel_case(self):
        assert convert_to_camel_case("foo_bar_baz") == "FooBarBaz"
        assert convert_to_camel_case("single") == "Single"

    def test_convert_to_camel_case_empty_string(self):
        # Implementation: "".split("_") -> [""]; capitalize yields "" so "x or '_'" gives "_"
        assert convert_to_camel_case("") == "_"

    def test_convert_to_camel_case_with_underscores(self):
        assert convert_to_camel_case("_leading") == "_Leading"

    def test_sort_url_by_stub_orders_by_stub(self):
        urls = [
            "https://cdn.example.com/z_last.m3u8",
            "https://cdn.example.com/a_first.m3u8",
            "https://cdn.example.com/m_middle.m3u8",
        ]
        result = sort_url_by_stub(urls)
        assert result[0].endswith("a_first.m3u8")
        assert result[1].endswith("m_middle.m3u8")
        assert result[2].endswith("z_last.m3u8")

    def test_sort_url_by_stub_mutates_in_place(self):
        urls = [
            "https://cdn.example.com/b.m3u8",
            "https://cdn.example.com/a.m3u8",
        ]
        result = sort_url_by_stub(urls)
        assert result is urls
        assert urls[0].endswith("a.m3u8")
        assert urls[1].endswith("b.m3u8")
