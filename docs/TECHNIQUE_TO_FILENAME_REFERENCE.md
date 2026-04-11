# Technique-to-Filename Reference

This document is the **authoritative reference** for how the **file-name-decipher** Lambda maps video file stems (URL stubs: lowercase, no extension) to curriculum techniques. It was reverse-engineered from `lambdas/file-name-decipher/` and `common/*_mappings.py`.

---

## How the convention works

### 1. Stem extraction

- The Lambda receives a file URL (e.g. from SNS after MediaConvert completes).
- The **file stem** is taken from the URL path: last path segment, lowercased, **without extension**.
- Example: `.../a0101x.m3u8` → stem `a0101x`.

### 2. Art routing (first character)

Routing is **only** by the **first character** of the stem:

| First character | Art        | DynamoDB table (env)                |
|-----------------|------------|-------------------------------------|
| `a`             | Aikido     | `AWS_DDB_AIKIDO_TABLE_NAME`         |
| `b`             | Battodo    | `AWS_DDB_BATTODO_TABLE_NAME`       |
| `d`             | Danzan Ryu | `AWS_DDB_DANZAN_RYU_TABLE_NAME`     |
| Any other      | Invalid    | Lambda raises `RuntimeError`        |

**Convention:** Only three prefixes are used. Aikido is prefixed by **`a`**. Battodo is prefixed by **`b`**. Danzan Ryu is prefixed by **`d`**. A file is never prefixed by any other character.

### 3. Variation letter

- Most stems end with a **variation letter** (e.g. `a`–`y` or `a`–`z` depending on art/scroll).
- The Lambda uses this letter to distinguish multiple video variations for the same technique; it does not change which technique is selected.

### 4. Scroll / technique selection

- After routing to an art, the stem is parsed with **scroll-specific regexes** and lookup tables.
- The result is an offset into that scroll’s item list in DynamoDB; the HLS URL is stored in that item’s variations.

---

## Aikido

- **Prefix:** `a` only.
- **Scroll:** Determined by the two digits immediately after `a`: `a01`–`a43` → see `AIKIDO_SCROLL_LOOKUP` in `common/aikido_mappings.py`.
- **Technique:** For scrolls that support it, two digits (and sometimes more) after the scroll code; then a single **variation letter** `[a-z]`.

### Aikido: technique (scroll) → filename pattern

Every Aikido scroll and its regex pattern is below. “Filename format” describes the stem; “Example” is one valid stem.

| # | Scroll (technique)     | Regex pattern (from code)   | Filename format              | Example   |
|---|------------------------|-----------------------------|------------------------------|-----------|
| 1 | bo_drills              | `^a01([0-9]{2})[a-z]$`      | `a01` + 2-digit technique + letter | `a0101x`  |
| 2 | bo_kata                 | `^a02([0-9]{2})[a-z]$`      | `a02` + 2-digit technique + letter | `a0205z`  |
| 3 | bo_kumite               | `^a03([0-9]{2})[a-z]$`      | `a03` + 2-digit technique + letter | `a0301a`  |
| 4 | bo_strikes             | `^a04([0-9]{2})[a-z]$`      | `a04` + 2-digit technique + letter | `a0402b`  |
| 5 | club_and_knife         | `^a05`                      | `a05` + (suffix per curriculum)    | `a05...`  |
| 6 | futaridori             | `^a06([0-9]{2})[a-z]$`      | `a06` + 2-digit technique + letter | `a0610a`  |
| 7 | gaiden                 | `^a07`                      | `a07` only                    | `a07`     |
| 8 | gokajo                 | `^a08`                      | `a08` only                    | `a08`     |
| 9 | goshin                 | `^a09`                      | `a09` only                    | `a09`     |
|10 | gun                    | `^a10`                      | `a10` only                    | `a10`     |
|11 | happo_zanshin          | `^a11`                      | `a11` only                    | `a11`     |
|12 | hijiate                | `^a12([0-9]{2})[a-z]$`      | `a12` + 2-digit technique + letter | `a1201a`  |
|13 | hijikime               | `^a13([0-9]{2})[a-z]$`      | `a13` + 2-digit technique + letter | `a1302b`  |
|14 | hijishime              | `^a14([0-9]{2})[a-z]$`      | `a14` + 2-digit technique + letter | `a1401c`  |
|15 | ikkajo                 | `^a15([0-9]{2})[a-z]$`      | `a15` + 2-digit technique + letter | `a1515z`  |
|16 | iriminage              | `^a16([0-9]{2})[a-z]$`      | `a16` + 2-digit technique + letter | `a1601a`  |
|17 | jiyu                   | `^a17([0-9]{2})[a-z]$`      | `a17` + 2-digit technique + letter | `a1703a`  |
|18 | jo                     | `^a18([0-9]{2})[a-z]$`      | `a18` + 2-digit technique + letter | `a1805a`  |
|19 | jujinage               | `^a19([0-9]{2})[a-z]$`      | `a19` + 2-digit technique + letter | `a1901a`  |
|20 | kaiten_nage            | `^a20([0-9]{2})[a-z]$`      | `a20` + 2-digit technique + letter | `a2002a`  |
|21 | katate_mochi           | `^a21([0-9]{2})[a-z]$`      | `a21` + 2-digit technique + letter | `a2101a`  |
|22 | kokyunage              | `^a22`                      | `a22` only                    | `a22`     |
|23 | koshinage              | `^a23([0-9]{2})[a-z]$`      | `a23` + 2-digit technique + letter | `a2301a`  |
|24 | kotegaeshi             | `^a24([0-9]{2})[a-z]$`      | `a24` + 2-digit technique + letter | `a2403a`  |
|25 | nikajo                 | `^a25([0-9]{2})[a-z]$`      | `a25` + 2-digit technique + letter | `a2510a`  |
|26 | push_pull              | `^a26`                      | `a26` only                    | `a26`     |
|27 | ryote_mochi            | `^a27([0-9]{2})[a-z]$`      | `a27` + 2-digit technique + letter | `a2701a`  |
|28 | sabaki                 | `^a28([0-9]{2})[a-z]$`      | `a28` + 2-digit technique + letter | `a2801a`  |
|29 | sankajo                | `^a29([0-9]{2})[a-z]$`      | `a29` + 2-digit technique + letter | `a2901a`  |
|30 | shihonage              | `^a30([0-9]{2})[a-z]$`      | `a30` + 2-digit technique + letter | `a3001a`  |
|31 | shomen_uchi            | `^a31([0-9]{2})[a-z]$`      | `a31` + 2-digit technique + letter | `a3101a`  |
|32 | shomen_uchi_advanced   | `^a32([0-9]{2})[a-z]$`      | `a32` + 2-digit technique + letter | `a3201a`  |
|33 | sokumen_iriminage     | `^a33([0-9]{2})[a-z]$`      | `a33` + 2-digit technique + letter | `a3301a`  |
|34 | tai_otoshi             | `^a34`                      | `a34` only                    | `a34`     |
|35 | ten_chi_nage           | `^a35([0-9]{2})[a-z]$`      | `a35` + 2-digit technique + letter | `a3501a`  |
|36 | tsuki                  | `^a36([0-9]{2})[a-z]$`      | `a36` + 2-digit technique + letter | `a3601a`  |
|37 | udegarami               | `^a37([0-9]{2})[a-z]$`      | `a37` + 2-digit technique + letter | `a3701a`  |
|38 | ukemi                  | `^a38([0-9]{2})[a-z]$`      | `a38` + 2-digit technique + letter | `a3801a`  |
|39 | ushironage             | `^a39`                      | `a39` only                    | `a39`     |
|40 | yama_arashi            | `^a40`                      | `a40` only                    | `a40`     |
|41 | yokomen_uchi_inside   | `^a41([0-9]{2})[a-z]$`      | `a41` + 2-digit technique + letter | `a4101a`  |
|42 | yonkajo                | `^a42([0-9]{2})[a-z]$`      | `a42` + 2-digit technique + letter | `a4201a`  |
|43 | yubi                   | `^a43`                      | `a43` only                    | `a43`     |

Scrolls with “only” in the format (e.g. `a07`, `a22`) have no capturing group in the regex; the Lambda’s technique lookup may not support them without additional logic.

**Source:** `common/aikido_mappings.py` (`AIKIDO_REGEX_LOOKUP`, `AIKIDO_SCROLL_LOOKUP`), `lambdas/file-name-decipher/aikido.py`.

---

## Battodo

- **Prefix:** `b` only. (Scroll is determined by further characters in the stem; see Battodo mappings in code.)
- **Variation:** Last character is typically `[a-y]` (Battodo code often excludes `z`) or `[a-z]` depending on scroll.

### Battodo: scroll → first char and filename pattern

| Scroll              | First char | Filename pattern (stem)                    | Regex / logic (from code)           | Example    |
|---------------------|------------|--------------------------------------------|--------------------------------------|------------|
| toyama_ryu          | `a`        | Not reachable (Aikido claims `a`)          | `^a([0-9]{2})([0-9]{2})[a-z]$`       | —          |
| tameshigiri         | `b`        | `b` + 2-digit rank + 2-digit technique + letter | `^b([0-9]{2})([0-9]{2})[a-z]$`  | `b0101a`   |
| shodan_uchi_waza    | `c`        | `c` + 2-digit section + `[a-y]`           | `^c([0-9]{2})([a-y]{1})$`            | `c05a`     |
| shodan_no_waza      | `d`        | `d` + 2-digit set + defense char + `[a-y]`| `^d([0-9]{2})([a-z])[a-y]$`          | `d01ua`    |
| sayu_giri           | `e`        | `e` + 2-digit section + `[a-y]`           | `^e([0-9]{2})([a-y]{1})$`            | `e05a`     |
| sandan_uchi_waza    | `f`        | `f` + 2-digit section + `[a-y]`           | `^f([0-9]{2})([a-y]{1})$`            | `f05a`     |
| sandan_sabaki       | `g`        | `g` + cut char + footwork char + `[a-y]`   | `^g([a-z])([a-z])([a-y])$`           | `gksa`     |
| sandan_no_waza      | `h`        | Set 01: `h01` + suffix; Set 02: `h02` + technique + level + letter | `^h([0-9]{2}).*$` / `^h([0-9]{2})([a-z])([a-z])[a-z]$` | `h01...`, `h02jka` |
| randori_okuden      | `i`        | `i` + 2-digit set + technique letter + letter | `^i([0-9]{2})([a-z])[a-z]$`      | `i01na`    |
| nidan_no_waza       | `j`        | `j` + 2-digit set + technique + level/tsuki + `[a-y]` | `^j([0-9]{2})([a-z])([a-z])[a-y]$` | `j01rna`   |
| kata                | `k`        | `k` + 2-digit kata number + letter         | `^k([0-9]{2})[a-z]$`                 | `k01a`     |
| battoho             | `l`        | `l` + 2-digit technique + 2-digit level + letter | `^l([0-9]{2})([0-9]{2})[a-z]$`   | `l0102a`   |
| formalities         | `m`        | `m` + 2-digit number + letter              | `^m([0-9]{2})[a-z]$`                 | `m05a`     |

### Battodo: character lookups (technique/level/defense from stem)

| Scroll / use        | Char position / meaning | Lookup (in `common/battodo_mappings.py`)     | Valid chars → value |
|---------------------|-------------------------|---------------------------------------------|---------------------|
| sandan_sabaki       | Cut type                | `SUBURI_SANDAN_SABAKI_CUT_TYPE`             | `k`→Kesa, `g`→Kiriage, `y`→Yoko |
| sandan_sabaki       | Footwork                | `SUBURI_SANDAN_SABAKI_FOOTWORK_TYPE`        | `f`→Shuffle, `s`→Step, `t`→2Step |
| shodan_no_waza      | Defense                 | `KUMITACHI_SHODAN_NO_WAZA_DEFENSE`          | `u,k,i,a,o,h,s,g` → Sankaku Uke, Kirigaeshi, … |
| sandan_no_waza (set 2), nidan_no_waza, etc. | Level | `KUMITACHI_LEVEL`                           | `k`→Kihon, `i`→Kihon Ichi, `n`→Kihon Ni, `j`→Jokyu, `g`→Goshin, `r`→Randori |
| sandan_no_waza set 2 | Technique              | `KUMITACHI_SANDAN_NO_WAZA_TECHNIQUE`        | `j`→Jochuge, `u`→Umote Ura, `g`→Gyo So |
| randori_okuden      | Technique               | `KUMITACHI_RANDORI_OKUDEN_TECHNIQUE`        | `n,c,s,a,i` → Nagare, Cut-for-Cut, … |
| nidan_no_waza       | Technique               | `KUMITACHI_NIDAN_NO_WAZA_TECHNIQUE`         | `i,r,o,m,h,s,k,j,n` → Tsuki, Inshin Irimi, … |
| nidan_no_waza (Set 1 Tsuki) | Tsuki level        | `KUMITACHI_NIDAN_NO_WAZA_TSUKI_TECHNIQUE`   | `s,g,o,j,r` → Sayu Uke, Tsukigote, … |
| kata                | Kata number             | `KATA_NAME`                                 | `01`–`06` → Happo no Kamae, … |
| battoho             | Technique               | `KATA_TECHNIQUE`                             | `01`–`08` → Ipponme, … |
| battoho             | Level                   | `KATA_BATTOHO_LEVEL`                         | `01`–`09` → Kihon, Jokyu, … |
| toyama_ryu          | Level                   | `KATA_TOYAMA_RYU_LEVEL`                      | `01`–`05` → Gunto Soho, … |
| tameshigiri         | Rank                    | `TAMESHIGIRI_RANK`                           | `01`–`09` → Yondan, … |
| tameshigiri         | Technique (per rank)    | `TAMESHIGIRI_TECHNIQUE[rank]`               | Index by rank; technique number in stem |

**Source:** `common/battodo_mappings.py`, `lambdas/file-name-decipher/battodo.py`.

---

## Danzan Ryu

- **Routing:** Only stems whose **first character is `d`** are sent to the Danzan Ryu Lambda. So only the scroll for `d` is ever used: **advanced_yawara**.
- **Handler for advanced_yawara:** `handle_simple_table_model`: strip first character, then find one or more digits in the remainder; that number (as string) is the “Number” key used to find the table row.

### Danzan Ryu: reachable scroll (current routing)

| Scroll           | First char | Filename pattern (stem)     | Logic (from code)                          | Example |
|------------------|------------|-----------------------------|--------------------------------------------|---------|
| advanced_yawara  | `d`        | `d` + digits [+ optional letter] | Remove first char; extract number; match by `Number` in table | `d11`, `d05a` |

No other first character reaches Danzan Ryu, so no other scroll in `DANZAN_RYU_SCROLL_DICT` is used by the Lambda.

### Danzan Ryu: full scroll dict (for reference; most unreachable with current routing)

The mappings define scroll letters for other stems; only `d` is routed to Danzan Ryu by `app.py`.

| Scroll char | Scroll name       | Filename pattern if it were reachable     | Handler (in code)           |
|-------------|-------------------|-------------------------------------------|-----------------------------|
| `a`         | advanced_weapons  | `a` + weapon char + number                 | handle_advanced_weapons     |
| `b`         | ukemi             | `b` + number [+ letter]                   | handle_simple_table_model   |
| `c`         | yawara_stick      | (basic_weapons-style)                     | handle_basic_weapons        |
| `d`         | advanced_yawara   | `d` + number [+ letter]                   | handle_simple_table_model   |
| `e`         | kiai_no_maki      | —                                         | handle_simple_table_model   |
| `f`         | basic_knife       | `f` + set + technique (2 chars)           | handle_basic_weapons        |
| `g`         | goshin            | `g` + enter char + number                  | handle_goshin               |
| `h`         | shinyo            | —                                         | handle_simple_table_model   |
| `i`         | shinin            | —                                         | handle_simple_table_model   |
| `j`         | aikijutsu_nage    | —                                         | handle_simple_table_model   |
| `k`         | kdm               | `k` + KDM char + number                    | handle_kdm                  |
| `l`         | katsu_kappo       | `l` + section+number (digits)             | handle_katsu_kappo          |
| `m`         | daito_no_maki     | `m` + group + number                      | handle_daito_no_maki        |
| `n`         | basic_nage        | —                                         | handle_simple_table_model   |
| `o`         | oku               | —                                         | handle_simple_table_model   |
| `p`         | drills            | `p` + 2-digit group + 2-digit set + 2-digit technique + `[a-y]` | handle_drills |
| `q`         | kyu_shime         | —                                         | handle_simple_table_model   |
| `r`         | fujin_goshin      | —                                         | handle_simple_table_model   |
| `s`         | shime             | `s` + flow number + technique number      | handle_shime                |
| `t`         | basic_stick       | `t` + set + technique                      | handle_basic_weapons        |
| `u`         | basic_handgun     | `u` + set + technique                      | handle_basic_weapons        |
| `v`         | advanced_nage     | —                                         | handle_simple_table_model   |
| `w`         | shime_groundflow  | `w` + flow number                          | handle_shime_groundflow     |
| `x`         | exercises         | —                                         | handle_simple_table_model   |
| `y`         | basic_yawara      | —                                         | handle_simple_table_model   |
| `z`         | multiple_attackers| —                                         | handle_simple_table_model   |

**Weapon chars (advanced_weapons):** `f`→knife, `r`→rifle, `t`→stick, `u`→handgun.  
**KDM chars:** `i`→kick_defense, `k`→kick, `p`→punch, `u`→punch_defense.  
**Goshin enter:** `i`→inside, `o`→outside.  
**Drill groups:** `01`–`08` → Footwork, Striking, StrikeDefense, Nage, SelfDefense, MultipleAttackers, PushPull, KyuShime.

**Source:** `common/danzan_ryu_mappings.py`, `lambdas/file-name-decipher/danzan_ryu.py`.

---

## Summary table: technique → filename (by art)

| Art       | Prefix | Variation letter | Implementation / source files |
|-----------|--------|------------------|--------------------------------|
| Aikido    | `a`    | Last char `[a-z]` | `common/aikido_mappings.py`, `lambdas/file-name-decipher/aikido.py` |
| Battodo   | `b`    | Usually last char `[a-y]` or `[a-z]` per scroll | `common/battodo_mappings.py`, `lambdas/file-name-decipher/battodo.py` |
| Danzan Ryu| `d`    | Depends on scroll (e.g. optional) | `common/danzan_ryu_mappings.py`, `lambdas/file-name-decipher/danzan_ryu.py` |

---

## Lambda entry point

- **Router:** `lambdas/file-name-decipher/app.py` → `lambda_handler` → `extract_file_url` → `utils.get_stub`; then branch on `file_stem[0]` to `aikido.handle_aikido`, `battodo.handle_battodo`, or `danzan_ryu.handle_danzan_ryu`.

For more on input conventions and examples, see [FILE_NAMING_CONVENTIONS.md](FILE_NAMING_CONVENTIONS.md).
