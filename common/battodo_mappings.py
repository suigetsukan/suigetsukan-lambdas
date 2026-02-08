"""
Battodo curriculum mappings for file-name-decipher lambda.
Scroll names, technique names, and level mappings for parsing video file names.
"""

BATTODO_SCROLL_DICT = {
    "a": "toyama_ryu",
    "b": "tameshigiri",
    "c": "shodan_uchi_waza",
    "d": "shodan_no_waza",
    "e": "sayu_giri",
    "f": "sandan_uchi_waza",
    "g": "sandan_sabaki",
    "h": "sandan_no_waza",
    "i": "randori_okuden",
    "j": "nidan_no_waza",
    "k": "kata",
    "l": "battoho",
    "m": "formalities",
}

SUBURI_SANDAN_SABAKI_CUT_TYPE = {
    "k": "Kesa",
    "g": "Kiriage",
    "y": "Yoko",
}

SUBURI_SANDAN_SABAKI_FOOTWORK_TYPE = {
    "f": "Shuffle",
    "s": "Step",
    "t": "2Step",
}

KUMITACHI_LEVEL = {
    "k": "Kihon",
    "i": "Kihon Ichi",
    "n": "Kihon Ni",
    "j": "Jokyu",
    "g": "Goshin",
    "r": "Randori",
}

KUMITACHI_SHODAN_NO_WAZA_DEFENSE = {
    "u": "Sankaku Uke",
    "k": "Kirigaeshi",
    "i": "Maki Uchi",
    "a": "Kaeshi Uchi",
    "o": "Maki Osae",
    "h": "Harai Uke",
    "s": "Maki Otoshi",
    "g": "Hiji Gote",
}

KUMITACHI_SANDAN_NO_WAZA_TECHNIQUE = {
    "j": "Jochuge",
    "u": "Umote Ura",
    "g": "Gyo So",
}

KUMITACHI_RANDORI_OKUDEN_TECHNIQUE = {
    "n": "Nagare",
    "c": "Cut-for-Cut",
    "s": "Shikaku",
    "a": "Attack & Counter",
    "i": "Ai Uchi (Simultaneous Strike)",
}

KUMITACHI_NIDAN_NO_WAZA_TSUKI_TECHNIQUE = {
    "s": "Sayu Uke",
    "g": "Tsukigote",
    "o": "Joge",
    "j": "Jokyu",
    "r": "Randori",
}

KUMITACHI_NIDAN_NO_WAZA_TECHNIQUE = {
    "i": "Tsuki",
    "r": "Inshin Irimi",
    "o": "Soto Uchi",
    "m": "Mawari Kaiten",
    "h": "Harai Uke + Osae",
    "s": "Osae + Osae",
    "k": "Kote + Harai Uke Ura",
    "j": "Hiji Gote + Maki Otoshi",
    "n": "Nidan no Waza",
}

KATA_NAME = {
    "01": "Happo no Kamae",
    "02": "Sanbo no Kamae",
    "03": "Happo Giri",
    "04": "Shodan no Kata",
    "05": "Nidan no Kata",
    "06": "Sandan no Kata",
}

KATA_TECHNIQUE = {
    "01": "Ipponme",
    "02": "Nihonme",
    "03": "Sanbonme",
    "04": "Yonhonme",
    "05": "Gohonme",
    "06": "Ropponme",
    "07": "Nanahonme",
    "08": "Happonme",
}

KATA_BATTOHO_LEVEL = {
    "01": "Kihon",
    "02": "Jokyu",
    "03": "Santen Giri",
    "04": "Santen Mayoko",
    "05": "Goten Giri",
    "06": "Juji",
    "07": "Juji Jokyu",
    "08": "Mangetsu",
    "09": "Combination",
}

KATA_TOYAMA_RYU_LEVEL = {
    "01": "Gunto Soho",
    "02": "Battojutsu",
    "03": "Battodo",
    "04": "Battodo Jokyu",
    "05": "Kumitachi",
}

TAMESHIGIRI_RANK = {
    "01": "Yondan",
    "02": "Sandan",
    "03": "Nidan",
    "04": "Shodan",
    "05": "Ikkyu",
    "06": "Nikyu",
    "07": "Sankyu",
    "08": "Yonkyu",
    "09": "Gokyu",
}

TAMESHIGIRI_TECHNIQUE = {
    "Yondan": ["Gaiden"],
    "Sandan": ["Furiwakegiri", "Mangetsu", "Hangetsu"],
    "Nidan": ["Goten Giri", "Battoho Goten Giri", "Rokuten Giri", "Sayu Kiriage", "Sayu Yoko Giri"],
    "Shodan": ["Ichimonji Giri", "Kesa Yoko Giri", "Santen Mayoko", "Battoho Santen Mayoko"],
    "Ikkyu": ["Cumulative"],
    "Nikyu": ["Tsubamagaeshi", "Santen Giri", "Battoho Santen Giri", "Yoko Giri"],
    "Sankyu": ["Kiriage", "Sayu Kesa", "Battoho Jokyu"],
    "Yonkyu": ["Kesa Giri", "Battoho Kihon"],
    "Gokyu": ["Target stand demo"],
}
