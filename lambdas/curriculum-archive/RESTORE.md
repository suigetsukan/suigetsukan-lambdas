# Suigetsukan curriculum archive — how to read these files

This folder is a complete, self-describing copy of the Suigetsukan curriculum videos and
the information that says which technique each video shows. It was written so that
someone holding only these files, with no access to the original website or to any AWS
account, can find, watch, and if needed republish every video.

The dojo teaches three arts. Each art is organised into **scrolls** (a scroll is a named
group of techniques, such as `katate_mochi` in Aikido or `shime_groundflow` in Danzan
Ryu). Each scroll lists its **techniques** in order. Each technique may have one or more
**variations** — separate video recordings of that technique.

## What is here

```
snapshots/
  latest/            <- always the most recent snapshot; start here
  YYYY-MM-DD/        <- one dated snapshot per monthly run (same layout as latest/)
    manifest.csv     <- one row per video: art / scroll / technique / variation -> file
    manifest.json    <- the same rows plus when and from what they were generated
    tables/
      aikido.json.gz      <- the full Aikido curriculum table, as exported
      battodo.json.gz     <- the full Battodo curriculum table
      danzan_ryu.json.gz  <- the full Danzan Ryu curriculum table
    docs/
      RESTORE.md                        <- this file
      TECHNIQUE_TO_FILENAME_REFERENCE.md <- how a file name encodes scroll + technique
      aikido_mappings.py                <- scroll number -> scroll name (Aikido)
      battodo_mappings.py               <- letter/number -> scroll, technique, level (Battodo)
      danzan_ryu_mappings.py            <- letter -> scroll name (Danzan Ryu)
    VERSION          <- which version of the archiving code produced the snapshot
videos/
  <stem>.mov         <- the original master recording of every variation
```

The `videos/` files are stored in a cold storage tier (S3 Glacier Deep Archive) to keep
the cost near zero. They must be *restored* (12–48 hours) before they can be downloaded;
`snapshots/` is always immediately readable. If you are reading this from a local copy on
a drive, that step has already been done.

## Start with manifest.csv

`manifest.csv` is a plain spreadsheet with one row per video. Open it in any spreadsheet
program or text editor. Columns:

| Column | Meaning |
|--------|---------|
| `art` | `aikido`, `battodo`, or `danzan_ryu`. |
| `scroll` | The scroll (group of techniques) the video belongs to, e.g. `katate_mochi`. This is the scroll's key in the table file. |
| `technique_index` | The technique's position within the scroll's list, counting from 0. This is the position in the `map.Items` list of that scroll's record in the table file. |
| `technique_number` | The technique's number as shown on the site (the `Number` field, usually counting from 1). |
| `technique_name` | The technique's display name (`Name` for Aikido and Danzan Ryu, `Techniques` for Battodo). |
| `variation_index` | Which variation of that technique this is, counting from 0, in the order the site lists them. |
| `stem` | The video's file name without extension, e.g. `a2101a`. The whole naming scheme is explained in `TECHNIQUE_TO_FILENAME_REFERENCE.md`. |
| `hls_url` | The streaming address the website used for this variation. The last part of the path is `<stem>.m3u8`. Not needed to watch the archived master. |
| `source_key` | The master file for this video: `videos/<source_key>` in this archive. |
| `source_size_bytes` | Size of that master file. |
| `source_etag` | A checksum of the master as stored by the original system. Two rows with the same value are the same footage. |
| `source_last_modified` | When the master was last uploaded to the original system. |
| `status` | See below. |

`status` tells you whether the row is complete:

- **`mapped`** — the technique has a video and the master file is in `videos/`. This is the normal case.
- **`missing_source`** — the curriculum table lists a video for this technique but no master file with that name existed when the snapshot was taken. The website may have been streaming a rendition whose master was deleted or renamed. Nothing can be recovered for that row from this archive; the row is kept so the gap is visible.
- **`orphan_source`** — a master file exists in `videos/` that no technique refers to. `art` is guessed from the first letter of the file name; `scroll`, `technique_*` and `variation_index` are blank. It is probably an unused take, a superseded recording, or a file uploaded under a name the site never linked. It is archived anyway; decide for yourself whether it is worth keeping.

Rows are sorted by art, then scroll, then technique, then variation, so reading the file
top to bottom walks the whole curriculum in order. Orphan rows come after the mapped rows
of their art.

### Byte-identical files under different names are intentional

Several masters are the same recording stored under more than one stem — for example a
single clip that demonstrates one movement used by three different techniques. They show
up as separate rows with identical `source_size_bytes` and `source_etag`. This is a
deliberate reuse of one clip, not a copying error; keep all of them so every technique
still has its video.

## How a file name says which technique it is

Every video's name (its `stem`) encodes the art, scroll, technique, and variation:

- The **first letter** is the art: `a` = Aikido, `b` = Battodo, `d` = Danzan Ryu.
- The **last letter** is usually the variation (`a`, `b`, `c` …).
- What is in between identifies the scroll and technique, differently per art.

`TECHNIQUE_TO_FILENAME_REFERENCE.md` (in this folder) spells out every pattern, and the
three `*_mappings.py` files are the exact lookup tables the site used — they are plain
Python dictionaries and read fine as text. Example: `a2101a` = Aikido (`a`), scroll 21
(`katate_mochi` in `aikido_mappings.py`), technique 01, variation `a`.

You rarely need to decode a name by hand: the manifest has already done it for every file.
The reference is here so the scheme survives even if the manifest is lost.

## How the table files are structured

`tables/<art>.json.gz` is a gzip-compressed JSON file (decompress with `gunzip`, 7-Zip, or
any archive tool). It contains a list of **scroll records** exactly as the website's
database held them. Each record looks like this (Danzan Ryu example, trimmed):

```json
{
  "id": "…",
  "Name": "shime_groundflow",
  "map": {
    "Name": "shime_groundflow",
    "Items": [
      {
        "Number": "1",
        "Name": "Groundflow1",
        "Description": "Complete flow",
        "Variations": [
          "https://…/hls/dw1a.m3u8",
          "https://…/hls/dw1b.m3u8"
        ]
      }
    ]
  }
}
```

- `Name` is the scroll key (the manifest's `scroll` column).
- `map.Items` is the ordered list of techniques (the manifest's `technique_index` is the position in this list).
- Each technique has `Number`, a display name (`Name`, or `Techniques` for Battodo), sometimes extra fields the site showed (`Description`, `Rank`, `Goal`), and `Variations` — the list of streaming addresses, in the order the manifest's `variation_index` follows.
- Techniques with an empty `Variations` list have no video; they are in the tables but not in the manifest.
- The remaining fields (`id`, `_version`, `createdAt`, …) are database bookkeeping.

The tables hold everything the curriculum site displayed for each technique, so they are
also the record of the curriculum itself, independent of the videos.

## Watching or republishing the videos

The masters in `videos/` are ordinary QuickTime `.mov` files and play in any video player.

The website streamed each video as HLS (the `hls_url` addresses, ending in `.m3u8`). Those
streaming renditions were **not** archived because they can be regenerated from the
masters. If you ever need them again, either:

1. **Serve the master directly.** Any web server or video host can serve the `.mov`
   (or an MP4 converted from it with `ffmpeg -i <stem>.mov -c copy <stem>.mp4`). For a
   dojo-sized audience this is entirely adequate.
2. **Re-create HLS renditions.** Run the master through a transcoder: AWS Elemental
   MediaConvert (what the original site used: one HLS output group per file, output name
   `<stem>`), or `ffmpeg -i <stem>.mov -codec copy -hls_time 6 -hls_playlist_type vod
   <stem>.m3u8` locally. Name the playlist `<stem>.m3u8` and the manifest's `stem` column
   links it back to its technique.

## Provenance

`VERSION` records the git commit of the archiving code, the Lambda version, and the time the
snapshot was generated. `manifest.json` repeats the generation time and the names of the
original buckets. Dated snapshot folders are never modified after they are written;
`latest/` is rewritten each run.
