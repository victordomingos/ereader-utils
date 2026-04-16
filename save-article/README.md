# save-article.py

A command-line Python script that saves the content of any URL as an
**EPUB file** optimised for the **Xteink X4** e-reader (CrossPoint Reader
firmware, 480×800 px, 220 ppi, no touch).

It extracts the main article body from the page, discards menus, footers,
and advertising, and writes a clean, self-contained EPUB to the `articles/`
folder.

---

## Output structure

```
articles/
  fernando-pessoa.epub
  shortwave-radio.epub
  my-custom-title.epub
```

---

## Requirements

- Python 3.10 or later
- Internet access

All Python dependencies are installed automatically on first run:

| Package          | Purpose                        |
|------------------|--------------------------------|
| `requests`       | HTTP downloads                 |
| `beautifulsoup4` | HTML article extraction        |
| `lxml`           | HTML parser backend            |
| `Pillow`         | Image processing               |

The script also makes opportunistic use of `curl` and `links` (the
text-mode browser) if they are available on your system, as fallbacks
for sites behind Cloudflare or other bot-detection layers.

---

## Installation

No installation step is required. Just place `save-article.py` in any
directory and run:

```bash
python3 save-article.py URL
```

Dependencies will be installed automatically via `pip` on first run.

---

## Usage

```
python3 save-article.py [URL] [options]
```

### Options

| Option | Description |
|---|---|
| `URL` | URL of the page to save. |
| `-t TEXT`, `--title TEXT` | Custom title. Default: detected from the page. |
| `-o FILE`, `--output FILE` | Output path. Default: `articles/<title>.epub`. |
| `-l`, `--list` | List saved articles and exit. |
| `-d`, `--detailed` | With `-l`: show date and file size. |
| `--no-images` | Do not download images (faster, smaller file). |

### Examples

```bash
# Save a Wikipedia article
python3 save-article.py https://en.wikipedia.org/wiki/Fernando_Pessoa

# Save with a custom title
python3 save-article.py https://en.wikipedia.org/wiki/Shortwave_radio -t "Shortwave Radio"

# Save to a specific path
python3 save-article.py https://www.example.com/article -o articles/my_article.epub

# Save without images
python3 save-article.py https://www.example.com/article --no-images

# List saved articles
python3 save-article.py -l

# List with date and file size
python3 save-article.py -l -d
```

---

## Image handling

By default, images are downloaded alongside the article text. Downloaded
images are:

- Converted to **greyscale PNG** (suited for e-ink screens).
- Resized to fit within **440 × 700 px**.
- Filtered to reject known ad/tracking domains, decorative images,
  banners, and images smaller than 50 × 50 px.

Use `--no-images` to skip image downloading entirely for a faster result
and a smaller file.

---

## Content extraction

The script applies several layers of noise reduction before writing the EPUB:

- Standard HTML boilerplate (navbars, footers, sidebars, social buttons,
  cookie notices, GDPR banners) is removed via CSS selectors.
- The article body is isolated using common semantic selectors (`article`,
  `main`, `.article-body`, `.mw-parser-output`, etc.), falling back to
  `<body>` if none match.
- Consecutive duplicate text blocks are deduplicated.
- Hero images before the first paragraph and thumbnail images after the
  last paragraph are trimmed.

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

## Title detection

If no `-t` title is supplied, the script attempts to detect one from the
page in the following order:

1. `og:title` meta tag
2. `<title>` element (site suffix stripped)
3. First `<h1>` element
4. Last path segment of the URL

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
