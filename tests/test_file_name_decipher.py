"""
Tests for file-name-decipher: app, aikido, battodo, danzan_ryu logic.
"""

import json
import pytest

import aikido
import app
import battodo
import danzan_ryu


class TestAppExtractFileUrl:
    """Tests for app.extract_file_url."""

    def test_extract_complete_notification(self):
        event = {
            "Records": [
                {
                    "Sns": {
                        "Subject": "Video Complete",
                        "Message": json.dumps({"hlsUrl": "https://cdn.example.com/vid.m3u8"}),
                    }
                }
            ]
        }
        assert app.extract_file_url(event) == "https://cdn.example.com/vid.m3u8"

    def test_extract_direct_notification(self):
        event = {
            "Records": [
                {
                    "Sns": {
                        "Subject": "Direct",
                        "Message": "https://cdn.example.com/direct.m3u8",
                    }
                }
            ]
        }
        assert app.extract_file_url(event) == "https://cdn.example.com/direct.m3u8"

    def test_extract_ingest_returns_none(self):
        event = {
            "Records": [
                {
                    "Sns": {
                        "Subject": "Ingest",
                        "Message": "{}",
                    }
                }
            ]
        }
        assert app.extract_file_url(event) is None

    def test_extract_unknown_raises(self):
        event = {
            "Records": [
                {
                    "Sns": {
                        "Subject": "Unknown",
                        "Message": "x",
                    }
                }
            ]
        }
        with pytest.raises(RuntimeError, match="Unknown notification type"):
            app.extract_file_url(event)


class TestAikidoLocateTechnique:
    """Tests for aikido.locate_technique_in_json (scrolls with numeric capture group)."""

    def test_locate_technique_bo_drills(self):
        json_data = [
            {"Number": "1", "Name": "First"},
            {"Number": "5", "Name": "Fifth"},
        ]
        offset = aikido.locate_technique_in_json("bo_drills", "a0101x", json_data)
        assert offset == 0

    def test_locate_technique_ikkajo(self):
        json_data = [
            {"Number": "1", "Name": "Ikkajo 1"},
            {"Number": "15", "Name": "Ikkajo 15"},
        ]
        offset = aikido.locate_technique_in_json("ikkajo", "a1515z", json_data)
        assert offset == 1

    def test_locate_technique_invalid_stem_raises(self):
        json_data = [{"Number": "1"}]
        with pytest.raises(RuntimeError, match="Could not find aikido pattern"):
            aikido.locate_technique_in_json("bo_drills", "x9999z", json_data)


class TestBattodoScrollHandler:
    """Tests for battodo.pick_battodo_scroll_handler."""

    def test_shodan_uchi_waza(self):
        json_data = [
            {"Number": "1", "Name": "Waza 1"},
            {"Number": "5", "Name": "Waza 5"},
        ]
        offset = battodo.pick_battodo_scroll_handler("shodan_uchi_waza", "c05a", json_data)
        assert offset == 1

    def test_sandan_sabaki(self):
        json_data = [
            {"Name": "Kesa", "Footwork": "Shuffle"},
            {"Name": "Kesa", "Footwork": "Step"},
            {"Name": "Kiriage", "Footwork": "Shuffle"},
        ]
        offset = battodo.pick_battodo_scroll_handler("sandan_sabaki", "gksa", json_data)
        assert offset == 1  # Kesa + Step

    def test_kumitachi_shodan_no_waza(self):
        json_data = [
            {"Set": "1", "Name": "Sankaku Uke"},
            {"Set": "1", "Name": "Kirigaeshi"},
            {"Set": "2", "Name": "Sankaku Uke"},
        ]
        offset = battodo.pick_battodo_scroll_handler("shodan_no_waza", "d01ua", json_data)
        assert offset == 0

    def test_kata(self):
        json_data = [
            {"Name": "Happo no Kamae"},
            {"Name": "Sanbo no Kamae"},
        ]
        offset = battodo.pick_battodo_scroll_handler("kata", "k01a", json_data)
        assert offset == 0

    def test_tameshigiri(self):
        json_data = [
            {"Rank": "Yondan", "Techniques": "Gaiden"},
        ]
        offset = battodo.pick_battodo_scroll_handler("tameshigiri", "b0101a", json_data)
        assert offset == 0

    def test_formalities(self):
        json_data = [
            {"Number": "1"},
            {"Number": "5"},
        ]
        offset = battodo.pick_battodo_scroll_handler("formalities", "m05a", json_data)
        assert offset == 1

    def test_invalid_scroll_raises(self):
        with pytest.raises(RuntimeError, match="Invalid Battodo scroll"):
            battodo.pick_battodo_scroll_handler("invalid", "x", [])


class TestDanzanRyuScrollHandler:
    """Tests for danzan_ryu.pick_danzan_ryu_scroll_handler."""

    def test_drills(self):
        json_data = [
            {"Group": "Footwork", "Set": "1", "Number": "1"},
            {"Group": "Footwork", "Set": "1", "Number": "5"},
        ]
        offset = danzan_ryu.pick_danzan_ryu_scroll_handler("drills", "p010105a", json_data)
        assert offset == 1

    def test_advanced_weapons(self):
        json_data = [
            {"Weapon": "knife", "Number": "1"},
            {"Weapon": "knife", "Number": "2"},
        ]
        offset = danzan_ryu.pick_danzan_ryu_scroll_handler("advanced_weapons", "af1a", json_data)
        assert offset == 0

    def test_kdm(self):
        json_data = [
            {"DrillType": "kick", "Number": "1"},
        ]
        offset = danzan_ryu.pick_danzan_ryu_scroll_handler("kdm", "kk1a", json_data)
        assert offset == 0

    def test_goshin(self):
        json_data = [
            {"Enter": "inside", "Number": "1"},
        ]
        offset = danzan_ryu.pick_danzan_ryu_scroll_handler("goshin", "gi1a", json_data)
        assert offset == 0

    def test_simple_table_model(self):
        json_data = [
            {"Number": "1"},
            {"Number": "05"},
        ]
        offset = danzan_ryu.pick_danzan_ryu_scroll_handler("ukemi", "b05a", json_data)
        assert offset == 1


class TestBattodoScrollName:
    """Tests for battodo scroll name lookup."""

    def test_get_battodo_scroll_name(self):
        assert battodo.get_battodo_scroll_name("a") == "toyama_ryu"
        assert battodo.get_battodo_scroll_name("j") == "nidan_no_waza"


class TestDanzanRyuScrollName:
    """Tests for danzan ryu scroll name lookup."""

    def test_get_danzan_ryu_scroll_name(self):
        assert danzan_ryu.get_danzan_ryu_scroll_name("a") == "advanced_weapons"
        assert danzan_ryu.get_danzan_ryu_scroll_name("p") == "drills"
