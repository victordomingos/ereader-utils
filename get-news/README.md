# get-news.py

A command-line Python script that downloads RSS/Atom feeds and generates
**EPUB files** optimised for the **Xteink X4** e-reader (CrossPoint Reader
firmware, 480×800 px, 220 ppi, no touch).

It reads the same `feed_config.lua` used by KOReader, so no extra
configuration is needed if you already have that file set up.

---

## Output structure

```
news/
  2026-04-16/
    portugal-news/
      rtp.epub
      publico.epub
    apple-mac/
      appleinsider-news.epub
    dotnet-microsoft/
      devblogs-microsoft-dotnet.epub
```

---

## Requirements

- Python 3.10 or later
- Internet access

All Python dependencies are installed automatically on first run:

| Package        | Purpose                          |
|----------------|----------------------------------|
| `requests`     | HTTP downloads                   |
| `feedparser`   | RSS / Atom parsing               |
| `beautifulsoup4` | HTML article extraction        |
| `lxml`         | HTML parser backend              |
| `Pillow`       | Image processing                 |

The script also makes opportunistic use of `curl` and `links` (the
text-mode browser) if they are available on your system, as fallbacks
for sites behind Cloudflare or other bot-detection layers.

---

## Installation

No installation step is required. Just place `get-news.py` and
`feed_config.lua` in the same directory and run:

```bash
python3 get-news.py
```

Dependencies will be installed automatically via `pip` on first run.

---

## Feed configuration

The script reads `feed_config.lua` — the same file used by KOReader's
built-in newsreader — from the current directory by default.

Each feed entry supports the following options:

| Option                  | Values          | Default | Description                                         |
|-------------------------|-----------------|---------|-----------------------------------------------------|
| `limit`                 | integer / `0`   | no limit | Maximum number of articles to process. `0` = no limit. |
| `download_full_article` | `true` / `false` | `false` | Download the full article page instead of using the feed summary. |
| `include_images`        | `true` / `false` | `false` | Download and embed images in the EPUB.              |
| `enable_filter`         | `true` / `false` | `false` | Apply a CSS selector to isolate the article body.   |
| `filter_element`        | CSS selector    | —       | CSS selector used when `enable_filter=true`.        |
| `block_element`         | CSS selector    | —       | CSS selector for elements to remove from the page.  |

Feeds are grouped into categories using Lua comments (lines starting with `--`).
The script uses the nearest comment heading above each feed entry as the
category name, which becomes the subfolder under the daily output directory.

**Example entry:**

```lua
{ "https://devblogs.microsoft.com/dotnet/feed/",
  limit = 20,
  download_full_article = true,
  include_images = true },
```

---

## Usage

```
python3 get-news.py [options]
```

### Options

| Option | Description |
|---|---|
| `-c FILE`, `--config FILE` | Path to KOReader LUA config file. Default: `feed_config.lua` |
| `-o DIR`, `--output DIR` | Output folder. Default: `news/` |
| `--only TEXT` | Process only feeds whose URL or category name contains TEXT. Repeatable. |
| `--list` | List all configured feeds and exit. |
| `-v`, `--verbose` | Show per-article diagnostics. Forces single-worker mode. |
| `-w N`, `--workers N` | Number of parallel feed workers. Default: one per feed. |
| `--cache FILE` | Path to the SQLite cache file. Default: `<output>/cache.db` |
| `--no-cache` | Disable cache and re-download everything. |
| `--feed-ttl MIN` | Minutes to cache feed responses. Default: `60`. Set to `0` to disable feed caching. |
| `-d N`, `--days N` | Number of days of output folders to keep. Default: `7`. |
| `--clean` | Remove output folders older than `--days` days, without downloading. |

### Examples

```bash
# Download all feeds
python3 get-news.py

# Use a KOReader config in a different location
python3 get-news.py -c ~/koreader/news/feed_config.lua

# Download only feeds matching "RTP" or "Publico"
python3 get-news.py --only RTP --only Publico

# Write EPUBs to a custom folder
python3 get-news.py -o ~/Desktop/news

# List all configured feeds without downloading
python3 get-news.py --list

# Remove output folders older than 7 days
python3 get-news.py --clean

# Force re-download, ignoring the cache
python3 get-news.py --no-cache

# Verbose output (single-worker, per-article diagnostics)
python3 get-news.py --only Meshtastic -v
```

---

## Caching

The script maintains a **SQLite cache** (`news/cache.db` by default) with
three tables:

- **articles** — Downloaded and parsed article content (kept for `--days × 3` days).
- **feeds** — Raw feed responses (kept for `--feed-ttl` minutes; default 60).
- **epubs** — Generated EPUB binaries keyed by content hash.

The cache avoids re-downloading articles that were already processed in a
previous run. Use `--no-cache` to bypass it entirely, or `--feed-ttl 0` to
disable only feed-level caching.

Old cache entries are pruned automatically at the end of each successful run.

---

## Image handling

When `include_images=true` is set for a feed, downloaded images are:

- Converted to **greyscale PNG** (suited for e-ink screens).
- Resized to fit within **440 × 700 px**.
- Filtered to reject known ad/tracking domains, decorative images,
  banners, and images smaller than 50 × 50 px.

---

## Content filtering

The script applies several layers of noise reduction:

- Standard HTML boilerplate (navbars, footers, sidebars, social buttons,
  cookie notices, GDPR banners) is removed via CSS selectors before extraction.
- Article body is isolated using common semantic selectors (`article`,
  `main`, `.article-body`, etc.) or a custom `filter_element` if configured.
- Feeds with `filtrar_promocoes=true` in the config have promotional and
  deal-focused articles automatically skipped.

---

## HTTP fallback strategy

For sites that block simple requests, the script tries multiple fetch
strategies in order:

1. `urllib` with a `Links` browser User-Agent (bypasses many CDN checks)
2. `requests` with a Chrome User-Agent
3. `requests` with a `Links` User-Agent
4. `curl` with `Links` headers
5. `links -source` (native TLS, if installed)

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
