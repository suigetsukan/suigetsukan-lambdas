"""
Tests for common curriculum mappings (aikido, battodo, danzan_ryu).
"""

import pytest

from common.aikido_mappings import AIKIDO_REGEX_LOOKUP, AIKIDO_SCROLL_LOOKUP
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
from common.danzan_ryu_mappings import (
    DANZAN_RYU_DRILL_GROUPS,
    DANZAN_RYU_GOSHIN_DICT,
    DANZAN_RYU_KDM_DICT,
    DANZAN_RYU_SCROLL_DICT,
    DANZAN_RYU_WEAPON_DICT,
)


class TestAikidoMappings:
    """Tests for AIKIDO_REGEX_LOOKUP and AIKIDO_SCROLL_LOOKUP."""

    def test_regex_lookup_has_expected_scrolls(self):
        assert "bo_drills" in AIKIDO_REGEX_LOOKUP
        assert "ikkajo" in AIKIDO_REGEX_LOOKUP
        assert "yubi" in AIKIDO_REGEX_LOOKUP
        assert len(AIKIDO_REGEX_LOOKUP) == 43

    def test_scroll_lookup_matches_regex_keys(self):
        for scroll_id, scroll_name in AIKIDO_SCROLL_LOOKUP.items():
            assert scroll_name in AIKIDO_REGEX_LOOKUP

    def test_bo_drills_regex_matches(self):
        import re

        pattern = AIKIDO_REGEX_LOOKUP["bo_drills"]
        assert re.match(pattern, "a0101x")
        assert re.match(pattern, "a0142z")
        assert not re.match(pattern, "a0201x")

    def test_scroll_id_to_name(self):
        assert AIKIDO_SCROLL_LOOKUP[1] == "bo_drills"
        assert AIKIDO_SCROLL_LOOKUP[15] == "ikkajo"
        assert AIKIDO_SCROLL_LOOKUP[43] == "yubi"


class TestBattodoMappings:
    """Tests for battodo mapping constants."""

    def test_scroll_dict_all_chars(self):
        assert BATTODO_SCROLL_DICT["a"] == "toyama_ryu"
        assert BATTODO_SCROLL_DICT["b"] == "tameshigiri"
        assert BATTODO_SCROLL_DICT["m"] == "formalities"
        assert len(BATTODO_SCROLL_DICT) == 13

    def test_suburi_sandan_sabaki(self):
        assert SUBURI_SANDAN_SABAKI_CUT_TYPE["k"] == "Kesa"
        assert SUBURI_SANDAN_SABAKI_CUT_TYPE["g"] == "Kiriage"
        assert SUBURI_SANDAN_SABAKI_FOOTWORK_TYPE["f"] == "Shuffle"

    def test_kumitachi_levels(self):
        assert KUMITACHI_LEVEL["k"] == "Kihon"
        assert KUMITACHI_LEVEL["r"] == "Randori"

    def test_kumitachi_shodan_no_waza_defense(self):
        assert KUMITACHI_SHODAN_NO_WAZA_DEFENSE["u"] == "Sankaku Uke"

    def test_kata_names(self):
        assert KATA_NAME["01"] == "Happo no Kamae"
        assert KATA_NAME["06"] == "Sandan no Kata"

    def test_kata_technique(self):
        assert KATA_TECHNIQUE["01"] == "Ipponme"
        assert KATA_TECHNIQUE["08"] == "Happonme"

    def test_tameshigiri_rank_and_technique(self):
        assert TAMESHIGIRI_RANK["01"] == "Yondan"
        assert TAMESHIGIRI_RANK["09"] == "Gokyu"
        assert TAMESHIGIRI_TECHNIQUE["Yondan"] == ["Gaiden"]
        assert TAMESHIGIRI_TECHNIQUE["Sandan"][0] == "Furiwakegiri"


class TestDanzanRyuMappings:
    """Tests for danzan ryu mapping constants."""

    def test_scroll_dict(self):
        assert DANZAN_RYU_SCROLL_DICT["a"] == "advanced_weapons"
        assert DANZAN_RYU_SCROLL_DICT["p"] == "drills"
        assert DANZAN_RYU_SCROLL_DICT["z"] == "multiple_attackers"
        assert len(DANZAN_RYU_SCROLL_DICT) == 26

    def test_weapon_dict(self):
        assert DANZAN_RYU_WEAPON_DICT["f"] == "knife"
        assert DANZAN_RYU_WEAPON_DICT["u"] == "handgun"

    def test_kdm_dict(self):
        assert DANZAN_RYU_KDM_DICT["k"] == "kick"
        assert DANZAN_RYU_KDM_DICT["p"] == "punch"

    def test_goshin_dict(self):
        assert DANZAN_RYU_GOSHIN_DICT["i"] == "inside"
        assert DANZAN_RYU_GOSHIN_DICT["o"] == "outside"

    def test_drill_groups(self):
        assert DANZAN_RYU_DRILL_GROUPS["01"] == "Footwork"
        assert DANZAN_RYU_DRILL_GROUPS["08"] == "KyuShime"
