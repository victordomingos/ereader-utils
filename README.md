# ereader-utils

Command-line tools to fetch, clean, and package web content as EPUBs for
e-ink reading on devices such as the [Xteink X4](https://www.xteink.com/).

---

## Tools

### [save-article](save-article/)
Saves any web page as a clean EPUB. Extracts the main article body, removes
ads and boilerplate, and optionally embeds images converted to greyscale.

```bash
python3 save-article.py https://en.wikipedia.org/wiki/Shortwave_radio
python3 save-article.py https://www.example.com/article -t "My Title"
```

---

### [get-news](get-news/)
Downloads RSS/Atom feeds and generates one EPUB per feed, organised by
category and date. Reads the same `feed_config.lua` used by KOReader, so
no extra configuration is needed if you already have that file set up.

```bash
python3 get-news.py
python3 get-news.py --only BBC --only Reuters
```

---

### [wiki-to-read](wiki-to-read/)
Builds a browsable Wikipedia library from the official XML dump. Articles
are grouped into EPUBs by 3-letter prefix and navigable by title via the
NCX table of contents. Supports a featured-articles-only mode for a compact
curated selection.

```bash
python3 wiki-to-read.py -l en --download-dump
python3 wiki-to-read.py -l en -D   # featured articles only
```

---

### [build-dictionary](build-dictionary/)
Builds offline dictionary EPUBs from Wiktionary XML dumps. Each EPUB covers
a 3-letter prefix and includes grammatical class, gender, and up to three
definitions per entry. Supports Portuguese, English, Spanish, French, German,
Italian, Catalan, and Galician.

```bash
python3 build-dictionary.py -l en --download-dump
python3 build-dictionary.py -l en
```

---

## Requirements

- Python 3.10 or later
- Internet access (for downloading dumps and feeds)

Each tool installs its own dependencies automatically on first run via `pip`.
See the individual README in each folder for full details.

---

## A note on development approach

These scripts were largely developed through vibe coding in collaboration
with [Claude](https://claude.ai) (Anthropic). They were written iteratively
to solve a personal problem, with the primary goal of working correctly
rather than adhering to any particular standard of code quality,
architecture, or long-term maintainability.

They are shared here in the hope that they may be useful to others in
similar situations. No warranties are made as to their fitness for any
purpose beyond the one they were built for. Review the code before running
it in any environment you care about, and adapt it freely to your own needs.
