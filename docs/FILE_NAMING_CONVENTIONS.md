# Input File Naming Conventions

The **file-name-decipher** Lambda processes video filenames (URL stubs: lowercase, no extension) and routes them to curriculum-specific DynamoDB tables. Routing is based on the first character of the file stem.

**General rules:**
- File stems are lowercase with no extension.
- The last character is typically a variation letter (`a`–`y`).
- Stems are extracted from the URL path (e.g. `.../a0101x.m3u8` → `a0101x`).

---

## Art Routing (First Character)

| Prefix | Art       | Notes                                              |
|--------|-----------|----------------------------------------------------|
| `a`    | Aikido    | Scroll codes `a01`–`a43`                           |
| `b`–`m`| Battodo   | Excludes `a` and `d`; scroll char = first letter   |
| `d`    | Danzan Ryu| Scroll char = first letter                         |

---

## Aikido

**Pattern:** `a` + 2-digit scroll number + optional suffix + variation letter

| Component     | Format   | Description                                                                 |
|---------------|----------|-----------------------------------------------------------------------------|
| Prefix        | `a`      | Fixed                                                                      |
| Scroll number | `01`–`43`| See `AIKIDO_SCROLL_LOOKUP` in `common/aikido_mappings.py`                   |
| Suffix        | varies   | Technique number, etc.; format depends on scroll                            |
| Variation     | `[a-z]`  | Single letter (e.g. `a`–`y`)                                                |

### Scroll → Pattern Examples

| Scroll              | Regex Pattern         | Example  |
|---------------------|-----------------------|----------|
| bo_drills           | `a01` + 2 digits + letter | `a0101x` |
| bo_kata             | `a02` + 2 digits + letter | `a0205z` |
| ikkajo              | `a15` + 2 digits + letter | `a1515z` |
| club_and_knife      | `a05` + suffix        | `a05...` |
| gaiden, gokajo, …   | `a07`, `a08`, …       | Prefix only |

Full mappings: `common/aikido_mappings.py` → `AIKIDO_REGEX_LOOKUP`, `AIKIDO_SCROLL_LOOKUP`.

---

## Battodo

**Pattern:** Scroll letter + scroll-specific fields + variation letter

Scroll is determined by the first character and `BATTODO_SCROLL_DICT` in `common/battodo_mappings.py`.

| Scroll Char | Scroll            | Pattern                             | Example    |
|-------------|-------------------|-------------------------------------|------------|
| `a`         | toyama_ryu        | `a` + 2-digit technique + 2-digit level + letter | `a0101a` * |
| `b`         | tameshigiri       | `b` + 2-digit rank + 2-digit technique + letter | `b0101a`   |
| `c`         | shodan_uchi_waza  | `c` + 2-digit section + letter      | `c05a`     |
| `d`         | shodan_no_waza    | `d` + 2-digit set + defense char + variation | `d01ua`    |
| `e`         | sayu_giri         | `e` + 2-digit section + letter      | `e05a`     |
| `f`         | sandan_uchi_waza  | `f` + 2-digit section + letter      | `f05a`     |
| `g`         | sandan_sabaki     | `g` + cut + footwork + letter       | `gksa`     |
| `h`         | sandan_no_waza    | `h` + 2-digit set [+ technique + level + letter] | `h01...`, `h02jka` |
| `i`         | randori_okuden    | `i` + 2-digit set + technique + letter | `i01na`   |
| `j`         | nidan_no_waza     | `j` + 2-digit set + technique + level + letter | `j01rna`  |
| `k`         | kata              | `k` + 2-digit kata + letter         | `k01a`     |
| `l`         | battoho           | `l` + 2-digit technique + 2-digit level + letter | `l0102a` |
| `m`         | formalities       | `m` + 2-digit number + letter       | `m05a`     |

\* **Note:** `a`-prefixed files are routed to Aikido, not Battodo. Toyama Ryu may be unreachable with current routing.

### Character lookups (Battodo)

- **Cut type** (sandan_sabaki): `k`=Kesa, `g`=Kiriage, `y`=Yoko
- **Footwork**: `f`=Shuffle, `s`=Step, `t`=2Step
- **Kumitachi level**: `k`=Kihon, `i`=Kihon Ichi, `n`=Kihon Ni, `j`=Jokyu, `g`=Goshin, `r`=Randori
- **Defense** (shodan_no_waza): `u`, `k`, `i`, `a`, `o`, `h`, `s`, `g` → see `KUMITACHI_SHODAN_NO_WAZA_DEFENSE`
- **Techniques**: see `common/battodo_mappings.py` for full mappings

---

## Danzan Ryu

**Pattern:** Scroll letter + scroll-specific fields [+ variation letter]

Only stems starting with `d` are routed to Danzan Ryu; `d` maps to the `advanced_yawara` scroll. Other scroll letters exist in `DANZAN_RYU_SCROLL_DICT` but are not reached by the current routing.

| Scroll Char | Scroll          | Pattern                             | Example    |
|-------------|-----------------|-------------------------------------|------------|
| `d`         | advanced_yawara | See `handle_daito_no_maki` / simple | `d...`     |

### Scroll handlers and patterns

| Scroll            | Pattern                          | Example     |
|-------------------|----------------------------------|-------------|
| drills            | `p` + 2-digit group + 2-digit set + 2-digit technique + letter | `p010105a` |
| advanced_weapons  | scroll + weapon + number         | `af1a` (f=knife) |
| kdm               | scroll + drill type + number     | `kk1a` (k=kick) |
| goshin            | scroll + enter + number          | `gi1a` (i=inside) |
| shime             | scroll + flow + technique        | `s11a`      |
| daito_no_maki     | scroll + group + number          | `m11`       |
| shime_groundflow  | scroll + flow number             | `w1a`       |
| katsu_kappo       | scroll + section+number          | `l101`      |
| basic_weapons     | scroll + set + technique         | `t11` (t=stick) |
| simple_table_model| scroll + number                  | `b05a` (ukemi) |

**Weapon chars:** `f`=knife, `r`=rifle, `t`=stick, `u`=handgun  
**KDM chars:** `i`=kick_defense, `k`=kick, `p`=punch, `u`=punch_defense  
**Goshin chars:** `i`=inside, `o`=outside  

Full mappings: `common/danzan_ryu_mappings.py`.

---

## Summary

| Art       | Prefix(es) | Variation Letter | Source Files                                              |
|-----------|------------|------------------|-----------------------------------------------------------|
| Aikido    | `a`        | Last char `[a-z]`| `common/aikido_mappings.py`, `lambdas/file-name-decipher/aikido.py` |
| Battodo   | `b,c,e,f,g,h,i,j,k,l,m` | Last char `[a-y]` | `common/battodo_mappings.py`, `lambdas/file-name-decipher/battodo.py` |
| Danzan Ryu| `d`        | Depends on scroll| `common/danzan_ryu_mappings.py`, `lambdas/file-name-decipher/danzan_ryu.py` |
