# build-dictionary.py

A command-line Python script that builds **EPUB dictionaries** from
[Wiktionary](https://www.wiktionary.org/) XML dumps, optimised for the
**Xteink X4** e-reader ([CrossPoint Reader](https://github.com/crosspoint-reader/crosspoint-reader) firmware, 480×800 px, 220 ppi,
no touch).

Each EPUB covers a 3-letter prefix (e.g. `ABA`, `COM`, `PRE`) and is
organised internally into chapters by the 4th letter, navigable via the
NCX table of contents. The result is a compact offline dictionary library
that fits the constraints of an e-ink device with no internet access.

---

## Output structure

```
dictionaries/
  pt/
    A/
      AB/
        DICT_ABA.epub
        DICT_ABE.epub
    C/
      CO/
        DICT_COM.epub
  en/
    A/
      AB/
        DICT_ABO.epub
```

Each EPUB contains up to three definitions per entry, the grammatical
class, and gender where available.

---

## Requirements

- Python 3.10 or later
- Internet access (for dump download and `--update-since`)

All Python dependencies are installed automatically on first run:

| Package    | Purpose          |
|------------|------------------|
| `requests` | HTTP downloads   |

No other packages are required. The script uses only the Python standard
library for XML parsing, EPUB generation, and SQLite caching.

---

## Installation

No installation step is required. Just place `build-dictionary.py` in any
directory and run:

```bash
python3 build-dictionary.py -l pt --download-dump
```

Dependencies will be installed automatically via `pip` on first run.

---

## Supported languages

| Code | Language   | Wiktionary                          |
|------|------------|-------------------------------------|
| `pt` | Português  | https://pt.wiktionary.org           |
| `es` | Español    | https://es.wiktionary.org           |
| `en` | English    | https://en.wiktionary.org           |
| `fr` | Français   | https://fr.wiktionary.org           |
| `de` | Deutsch    | https://de.wiktionary.org           |
| `it` | Italiano   | https://it.wiktionary.org           |
| `ca` | Català     | https://ca.wiktionary.org           |
| `gl` | Galego     | https://gl.wiktionary.org           |

Run `python3 build-dictionary.py -i` to list languages and check which
dumps are already downloaded.

---

## Typical workflow

```bash
# 1. Download the dump for the language you want
python3 build-dictionary.py -l pt --download-dump

# 2. Build all EPUBs (this may take several minutes)
python3 build-dictionary.py -l pt

# 3. (Optional) Retry entries that had no definition on first pass
python3 build-dictionary.py -l pt -c
python3 build-dictionary.py -l pt
```

Dumps are stored in the `dumps/` folder. EPUBs are written to
`dictionaries/<lang>/`. Both are skipped on subsequent runs if they
already exist.

---

## Usage

```
python3 build-dictionary.py [options] [LETTER_OR_PREFIX ...]
```

### Options

| Option | Description |
|---|---|
| `-l CODE`, `--lang CODE` | Language code. Default: `pt`. Available: `pt es en fr de it ca gl` |
| `-i`, `--list-languages` | List supported languages and dump status, then exit. |
| `-d FILE`, `--dump FILE` | Path to a local XML or `.bz2` dump file. Default: `dumps/` folder. |
| `--download-dump` | Download the Wiktionary dump for the selected language and exit. |
| `-v`, `--verbose` | Diagnostic mode: show the real dump format and test `parse_entry()` on a few articles. |
| `-c`, `--clear-empty` | Delete cache entries that had no definition, so they are retried on the next run. |
| `-a DATE`, `--update-since DATE` | Delete cache entries and EPUBs for pages modified since `DATE` (format: `YYYY-MM-DD`). Uses the Wiktionary API. |
| `LETTER_OR_PREFIX` | One or more single letters (`A`), 2-letter prefixes (`AB`), or 3-letter prefixes (`ABA`). Without arguments, everything is processed. With `-a`, filters the update by letter. |

### Examples

```bash
# Download the English dump
python3 build-dictionary.py -l en --download-dump

# Generate all EPUBs for Spanish
python3 build-dictionary.py -l es

# Generate only letters A and B for Portuguese
python3 build-dictionary.py -l pt A B

# Generate only the prefix ABA for Portuguese
python3 build-dictionary.py -l pt ABA

# List all supported languages and dump status
python3 build-dictionary.py -i

# Diagnose the dump format (useful if no definitions are found)
python3 build-dictionary.py -l pt -v

# Clear entries without definition and rebuild
python3 build-dictionary.py -l pt -c
python3 build-dictionary.py -l pt

# Invalidate cache and EPUBs for pages changed since a date
python3 build-dictionary.py -l pt -a 2025-06-01
```

---

## Caching

The script maintains a **SQLite cache** (`dictionaries/<lang>/_cache.db`)
to avoid reprocessing articles on subsequent runs. Each entry in the cache
is one of:

- **Full entry** — word was found with at least one definition.
- **Empty** — word exists in the dump but has no definition for the target language. These can be cleared with `-c` to force a retry.
- **NULL** — a network or parse failure occurred; will be retried automatically on the next run.

The cache makes incremental builds fast: only new or invalidated entries
are reprocessed.

---

## Updating

To refresh the dictionary after a new Wiktionary dump is published:

```bash
# Re-download the dump (will prompt before overwriting)
python3 build-dictionary.py -l pt --download-dump

# Delete existing EPUBs and re-run, or use --update-since
# to target only recently changed pages
python3 build-dictionary.py -l pt -a 2025-06-01
python3 build-dictionary.py -l pt
```

---

## EPUB structure

Each EPUB file covers one 3-letter prefix and is structured as:

- **Index page** — lists all 4th-letter chapters with entry counts and the generation date.
- **Chapter pages** — one per 4th letter, each containing all matching entries.

Entries show the headword in bold, grammatical class and gender in grey,
and up to three numbered definitions.

---

## Licence

This project is released under the **MIT Licence** — see the `LICENSE` file for details.

---

## A note on development approach

This script was largely developed through vibe coding in collaboration
with [Claude](https://claude.ai) (Anthropic). It was written iteratively to
solve a personal problem, with the primary goal of working correctly rather
than adhering to any particular standard of code quality, architecture, or
long-term maintainability.

It is shared here in the hope that it may be useful to others in similar
situations. No warranties are made as to its fitness for any purpose beyond
the one it was built for. Review the code before running it in any environment
you care about, and adapt it freely to your own needs.
