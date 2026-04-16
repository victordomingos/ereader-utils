# wiki-to-read.py

A command-line Python script that builds **Wikipedia EPUB libraries** from
the official XML dumps, optimised for the **Xteink X4** e-reader ([CrossPoint Reader](https://github.com/crosspoint-reader/crosspoint-reader) firmware, 480×800 px, 220 ppi, no touch).

Each EPUB covers a 3-letter prefix (e.g. `ABA`, `FER`, `PES`) and contains
up to 20 articles per page, each directly navigable by title via the NCX
table of contents. Articles are cleaned of references, footnotes, infoboxes,
and other boilerplate before writing, leaving only readable prose.

---

## Output structure

```
wikipedia/
  pt/
    A/
      AB/
        WIKI_ABA.epub   ← NCX: Abade, Abadia, Abalado...
    F/
      FE/
        WIKI_FER.epub   ← NCX: Fernando Pessoa, Fernão de Magalhães...
  en/
    S/
      SH/
        WIKI_SHA.epub   ← NCX: Shakespeare, Shackleton...
```

---

## Requirements

- **Python 3.10 or later** (uses `match/case` and `pathlib`)
- Internet access (for dump download)

All Python dependencies are installed automatically on first run:

| Package    | Purpose          |
|------------|------------------|
| `requests` | HTTP downloads   |

No other packages are required. XML parsing, EPUB generation, and SQLite
caching all use the Python standard library.

---

## Installation

No installation step is required. Just place `wiki-to-read.py` in any
directory and run:

```bash
python3 wiki-to-read.py -l pt --download-dump
```

Dependencies will be installed automatically via `pip` on first run.

---

## Supported languages

| Code | Language   | Wikipedia dump                            |
|------|------------|-------------------------------------------|
| `pt` | Português  | https://dumps.wikimedia.org/ptwiki/latest |
| `en` | English    | https://dumps.wikimedia.org/enwiki/latest |
| `es` | Español    | https://dumps.wikimedia.org/eswiki/latest |

Run `python3 wiki-to-read.py -i` to list languages and check which dumps
are already downloaded.

---

## Typical workflow

```bash
# 1. Download the dump (large files: PT ~2 GB, EN ~22 GB compressed)
python3 wiki-to-read.py -l pt --download-dump

# 2. Build all EPUBs
python3 wiki-to-read.py -l pt

# 3. (Optional) Build featured articles only — much smaller result
python3 wiki-to-read.py -l pt -D
```

Dumps are stored in the `dumps/` folder. EPUBs are written to
`wikipedia/<lang>/`. Both are skipped on subsequent runs if they already
exist.

---

## Usage

```
python3 wiki-to-read.py [options] [LETTER_OR_PREFIX ...]
```

### Options

| Option | Description |
|---|---|
| `-l CODE`, `--lang CODE` | Language code. Default: `pt`. Available: `pt en es` |
| `-m N`, `--min-chars N` | Minimum wikitext length in characters. Default: `2000`. Ignored with `-D`. |
| `-D`, `--featured-only` | Process featured articles only ★. Much smaller output; ignores `--min-chars`. |
| `-B`, `--include-good` | With `-D`: also include good articles. |
| `-w N`, `--workers N` | Parallel worker processes for wikitext parsing. Default: `1`. Use `0` for automatic (`cpu_count-1`). |
| `-d FILE`, `--dump FILE` | Path to a local `.bz2` dump file. Default: `dumps/` folder. |
| `--download-dump` | Download the Wikipedia dump for the selected language and exit. |
| `-i`, `--list-languages` | List supported languages and dump status, then exit. |
| `-v`, `--verbose` | Diagnostic mode: show the first parsed articles and their block counts. |
| `--info` | Show progress cache statistics. |
| `LETTER_OR_PREFIX` | One or more single letters (`A`), 2-letter prefixes (`AB`), or 3-letter prefixes (`FER`). Without arguments, everything is processed. |

### Examples

```bash
# Download the English dump
python3 wiki-to-read.py -l en --download-dump

# Build all Portuguese Wikipedia EPUBs (≥2000 wikitext chars)
python3 wiki-to-read.py -l pt

# Build only articles with at least 5000 characters
python3 wiki-to-read.py -l pt -m 5000

# Build featured articles only (★) for English
python3 wiki-to-read.py -l en -D

# Build featured + good articles for Portuguese
python3 wiki-to-read.py -l pt -D -B

# Build only letters A and B
python3 wiki-to-read.py -l pt A B

# Build only the prefix FER
python3 wiki-to-read.py -l pt FER

# Use 4 parallel workers for faster parsing
python3 wiki-to-read.py -l pt -w 4

# Run with automatic worker count
python3 wiki-to-read.py -l pt -w 0

# List supported languages
python3 wiki-to-read.py -i

# Diagnose the first few parsed articles
python3 wiki-to-read.py -l pt -v

# Show progress cache statistics
python3 wiki-to-read.py -l pt --info
```

---

## Article filtering

### By length (`--min-chars`)

The default threshold of 2000 characters is a practical balance between
coverage and output size. Approximate reference values for Portuguese:

| `--min-chars` | Articles  | Approx. size |
|---------------|-----------|--------------|
| 500           | ~750,000  | ~14 GB       |
| 2000          | ~400,000  | ~7–8 GB      |
| 5000          | ~170,000  | ~3 GB        |

### Featured and good articles (`-D`, `-B`)

Wikipedia editors mark a small subset of articles as **featured** (★) —
the highest quality — and **good** — a step below. Using `-D` processes
only featured articles, producing a compact library ideal for devices with
limited storage:

| Language | Featured only | Featured + good |
|----------|--------------|-----------------|
| PT       | ~80 MB       | ~200 MB         |
| EN       | ~500 MB      | ~1 GB           |

---

## Parallel processing (`--workers`)

By default the script runs in single-process mode (`-w 1`), which is the
most reliable and easiest to interrupt. For large dumps, parallel workers
can significantly reduce parse time:

- `-w 2` to `-w 4` — recommended for machines with 4 or more cores.
- `-w 0` — automatically uses `cpu_count - 1`.

The XML reading loop always runs in the main process; only wikitext parsing
is parallelised.

---

## Progress cache

The script maintains a **SQLite cache** (`wikipedia/<lang>/_cache.db`) that
records which 3-letter prefix groups have already been written. On subsequent
runs, completed EPUBs are skipped. The cache also enables safe interruption
with Ctrl+C — progress is preserved and the run can be resumed.

Use `--info` to inspect the cache:

```bash
python3 wiki-to-read.py -l pt --info
```

---

## Content cleaning

Before writing each article, the script removes:

- Redirects and stub articles (below `--min-chars`).
- Standard boilerplate sections: references, footnotes, see-also, external
  links, bibliography, and gallery (configurable per language).
- Block templates, tables, file/image links, and category tags.
- Inline markup: wikilinks, citation templates, bold/italic markers, and
  HTML tags.

The result is plain, readable prose with section headings and list items
preserved.

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
