#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
save-article.py
Saves the content of any URL as an EPUB file in the articles/ folder.

Extracts the main text from the page (ignores menus, footers, ads)
and generates a simple EPUB optimised for a 4" e-reader.

Dependencies: requests, beautifulsoup4, lxml
  pip install requests beautifulsoup4 lxml

Usage:
  python3 save-article.py URL
  python3 save-article.py URL -t "Custom Title"
  python3 save-article.py URL -o articles/my_file.epub
  python3 save-article.py -l            # list saved articles
  python3 save-article.py -l -d         # list with date and size

Examples:
  python3 save-article.py https://pt.wikipedia.org/wiki/Fernando_Pessoa
  python3 save-article.py https://www.publico.pt/2025/01/01/artigo
  python3 save-article.py https://en.wikipedia.org/wiki/Shortwave_radio -t "Shortwave Radio"
"""

import os, re, sys, zipfile, unicodedata, io
from datetime import date, datetime
from urllib.parse import urlparse

# ── Install dependencies ──────────────────────────────────────────────────
_DEPS = {
    "requests":       "requests",
    "beautifulsoup4": "bs4",
    "lxml":           "lxml",
    "Pillow":         "PIL",
}

def install_deps():
    import subprocess
    for pkg, module in _DEPS.items():
        try:
            __import__(module)
        except ImportError:
            print(f"  Installing {pkg}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install",
                                   pkg, "-q"])

install_deps()
import requests
from bs4 import BeautifulSoup
from PIL import Image

# ── Configuration ─────────────────────────────────────────────────────────
OUTPUT_FOLDER = "articles"
TIMEOUT       = 15
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Exact browser Links headers — gets past sites that block bots
UA_LINKS = [
    ("User-Agent",      "Links (2.30; Darwin 25.5.0 x86_64; LLVM/Clang 15.0; text)"),
    ("Accept",          "*/*"),
    ("Accept-Language", "en,pt;q=0.2,*;q=0.1"),
    ("Accept-Encoding", "gzip, deflate, bzip2"),
    ("Accept-Charset",  "us-ascii,ISO-8859-1,utf-8"),
    ("Connection",      "keep-alive"),
]

def _is_valid_html(html: str) -> bool:
    if len(html) < 500:
        return False
    h = html.lower()
    if len(html) < 20_000:
        if "just a moment" in h and "enable javascript" in h:
            return False
        if "checking your browser" in h:
            return False
    return True


def fetch_html(url, timeout=20) -> str | None:
    """
    Downloads HTML with progressive fallback:
    0. urllib + Links UA (HTTP/1.1 — passes AppleInsider/Cloudflare)
    1. requests + Chrome UA
    2. requests + Links UA
    3. curl + Links UA
    4. links -source
    """
    import subprocess, urllib.request, gzip as _gzip

    # Attempt 0 — urllib (HTTP/1.1, passes AppleInsider)
    try:
        req = urllib.request.Request(url, headers=dict(UA_LINKS))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            if "gzip" in resp.headers.get("Content-Encoding", ""):
                data = _gzip.decompress(data)
            html = data.decode("utf-8", errors="replace")
            if _is_valid_html(html):
                return html
    except Exception:
        pass

    for headers in [UA, dict(UA_LINKS)]:
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code == 200:
                if r.encoding and r.encoding.lower() != "utf-8":
                    r.encoding = r.apparent_encoding
                if _is_valid_html(r.text):
                    return r.text
        except Exception:
            pass

    # curl with Links UA
    cmd = ["curl", "-s", "--max-time", str(timeout), "--compressed",
           "--location"]
    for k, v in UA_LINKS:
        cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
        html = res.stdout.decode("utf-8", errors="replace")
        if res.returncode == 0 and _is_valid_html(html):
            return html
    except Exception:
        pass

    # links -source — native TLS
    try:
        res = subprocess.run(
            ["links", "-source", url],
            capture_output=True, timeout=timeout + 5
        )
        html = res.stdout.decode("utf-8", errors="replace")
        if res.returncode == 0 and _is_valid_html(html):
            return html
    except Exception:
        pass

    return None

# Maximum dimensions for xteink x4 (480×800, margins included)
IMG_MAX_WIDTH  = 440
IMG_MAX_HEIGHT = 700

# Ad and tracking URL domain patterns to ignore
_AD_DOMAINS = re.compile(
    r"doubleclick\.net|googlesyndication|adservice|googleads|"
    r"amazon-adsystem|adsrvr\.org|adnxs\.com|criteo\.|rubiconproject|"
    r"pubmatic\.com|openx\.net|moatads|scorecardresearch|"
    r"quantserve|omtrdc\.net|pixel\.|tracking\.|analytics\.|"
    r"beacon\.|/ads?/|/advert|/banner|/promo/|/sponsor",
    re.IGNORECASE
)

def process_image(img_url):
    """
    Downloads, converts to greyscale PNG and resizes.
    Rejects ad images and banners by aspect ratio.
    Returns PNG bytes or None on failure.
    """
    if _AD_DOMAINS.search(img_url):
        return None
    try:
        r = requests.get(img_url, headers=UA, timeout=TIMEOUT, stream=True)
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if not any(t in ct for t in ("image/", "jpeg", "png", "gif", "webp")):
            return None
        data = r.content
        if len(data) < 500:
            return None
    except Exception:
        return None
    try:
        img = Image.open(io.BytesIO(data))
        if img.width < 50 or img.height < 50:
            return None
        ratio = img.width / max(img.height, 1)
        if ratio > 5 or ratio < 0.2:
            return None
        if img.mode in ("P", "PA"):
            img = img.convert("RGBA")
        img = img.convert("L")
        if img.width > IMG_MAX_WIDTH or img.height > IMG_MAX_HEIGHT:
            img.thumbnail((IMG_MAX_WIDTH, IMG_MAX_HEIGHT), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        return None

# CSS selectors for main content, tried in order; first with content wins
CONTENT_SELECTORS = [
    "article",
    "main",
    "[role='main']",
    ".mw-parser-output",        # Wikipedia
    ".article-body",
    ".article__body",
    ".story-body",
    ".post-content",
    ".entry-content",
    ".content-body",
    "#content",
    "#main-content",
    ".main-content",
]

# Tags always removed (menus, ads, footers, etc.)
TAGS_TO_REMOVE = [
    "nav", "header", "footer", "aside", "script", "style",
    "noscript", "iframe", "form", "button",
    ".mw-editsection",          # Wikipedia: "edit" links
    ".reflist", ".references",  # Wikipedia: references
    ".navbox", ".infobox",      # Wikipedia: side boxes
    ".sistersitebox",
    ".toc",                     # Wikipedia: table of contents
    "#toc",
    ".hatnote",
    "[role='navigation']",
    "[role='banner']",
    "[role='contentinfo']",
    "[aria-hidden='true']",
    ".ad", ".ads", ".advertisement",
    ".social-share", ".share-buttons",
    ".related-articles", ".recommended",
    ".comments", "#comments",
    ".cookie-notice", ".gdpr",
]

# ── Content extraction ────────────────────────────────────────────────────

def fetch_page(url):
    """Downloads the page with requests → curl fallback, returns BeautifulSoup."""
    print(f"  Fetching: {url}")
    html = fetch_html(url)
    if not html:
        print(f"  ✗ Could not retrieve the page (tried requests and curl)")
        sys.exit(1)
    return BeautifulSoup(html, "lxml")


def extract_title(soup, url):
    """Extracts the page title."""
    # og:title usually has the best title
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()
    # Page <title>
    if soup.title and soup.title.string:
        t = soup.title.string.strip()
        # Remove site suffix ("Article – Publisher", "Foo | Wikipedia")
        t = re.sub(r"\s*[|–—-]\s*.{3,40}$", "", t).strip()
        if t:
            return t
    # h1
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)
    # URL as fallback
    return urlparse(url).path.rstrip("/").split("/")[-1].replace("-"," ").replace("_"," ").title()


def remove_elements(soup, selectors):
    """Removes elements from soup by a list of CSS/tag selectors."""
    for sel in selectors:
        for el in soup.select(sel) if sel.startswith((".","#","[") or " " in sel) else soup.find_all(sel):
            el.decompose()


# Alt text words indicating decorative/stock images to skip.
# "Feature Desaturated" is MacRumors' specific pattern for stock images.
_DECORATIVE_ALT = re.compile(
    r"feature\s+desaturat|"
    r"\bgeneric\b|\bstock\s+image\b|"
    r"\bplaceholder\b|"
    r"\bheader[\s-]image\b|\bcover[\s-]image\b",
    re.IGNORECASE
)

def _get_image_src(img_el):
    """
    Extracts the real URL from an <img> tag, covering the most common
    lazy-loading patterns: src, data-src, data-lazy-src, data-original,
    data-srcset / srcset (uses the first entry, highest resolution).
    Returns an absolute http(s) URL or an empty string if not found.
    """
    # Direct attributes (order: most specific to most generic)
    candidates = [
        img_el.get("data-src"),
        img_el.get("data-lazy-src"),
        img_el.get("data-original"),
        img_el.get("data-full-src"),
        img_el.get("data-img-src"),
    ]

    # srcset / data-srcset: "url1 800w, url2 400w" — pick highest resolution
    for attr in ("srcset", "data-srcset", "data-lazy-srcset"):
        val = img_el.get(attr, "")
        if val:
            # Each entry: "url [descriptor]"
            # Sort by numeric descriptor descending (highest resolution first)
            entries = [e.strip().split() for e in val.split(",") if e.strip()]
            def _weight(e):
                try:
                    return int(e[1].rstrip("wx")) if len(e) > 1 else 0
                except Exception:
                    return 0
            entries.sort(key=_weight, reverse=True)
            if entries and entries[0]:
                candidates.append(entries[0][0])
            break

    # src as last resort (may be a data: placeholder)
    candidates.append(img_el.get("src", ""))

    for src in candidates:
        if not src:
            continue
        if src.startswith("data:"):
            continue
        if src.startswith("//"):
            return "https:" + src
        if src.startswith("http"):
            return src

    return ""


def extract_content(soup, include_images=True):
    """
    Finds the main content block and converts it to a list of blocks.
    Types: {"type": "h1"|"h2"|"h3"|"p"|"li"|"img", "text": str}
    For images: {"type": "img", "name": str, "alt": str, "data": bytes}
    """
    remove_elements(soup, TAGS_TO_REMOVE)

    container = None
    for sel in CONTENT_SELECTORS:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 200:
            container = el
            break

    if not container:
        container = soup.find("body") or soup

    blocks      = []
    processed   = set()
    img_counter = [0]

    def process(el):
        if id(el) in processed:
            return
        processed.add(id(el))
        tag = getattr(el, "name", None)
        if not tag:
            return

        if tag in ("h1","h2","h3","h4"):
            text = el.get_text(" ", strip=True)
            if text and len(text) < 200:
                blocks.append({"type": tag, "text": clean_text(text)})

        elif tag == "p":
            if include_images:
                for img_el in el.find_all("img"):
                    _process_img(img_el)
            text = el.get_text(" ", strip=True)
            if text and len(text) > 20:
                blocks.append({"type": "p", "text": clean_text(text)})

        elif tag == "img":
            if include_images:
                _process_img(el)

        elif tag == "figure":
            if include_images:
                for img_el in el.find_all("img"):
                    _process_img(img_el)
            cap = el.find("figcaption")
            if cap:
                t = cap.get_text(" ", strip=True)
                if t:
                    blocks.append({"type": "p", "text": clean_text(t)})

        elif tag in ("ul","ol"):
            for li in el.find_all("li", recursive=False):
                text = li.get_text(" ", strip=True)
                if text and len(text) > 5:
                    blocks.append({"type": "li", "text": clean_text(text)})

        elif tag in ("div","section","article","main"):
            for child in el.children:
                if hasattr(child, "name"):
                    process(child)

    def _process_img(img_el):
        src = _get_image_src(img_el)
        if not src:
            return
        alt = clean_text(img_el.get("alt", ""))
        if alt and _DECORATIVE_ALT.search(alt):
            return
        data = process_image(src)
        if data:
            img_counter[0] += 1
            name = f"img{img_counter[0]:04d}.png"
            blocks.append({"type": "img", "name": name,
                           "alt": alt, "data": data})

    process(container)

    # Remove consecutive duplicates (preserve images)
    result   = []
    last_txt = None
    for b in blocks:
        if b["type"] == "img":
            result.append(b)
            last_txt = None
        elif b.get("text") != last_txt:
            result.append(b)
            last_txt = b.get("text")

    # 1. Remove images with empty alt before the first paragraph (hero images)
    first_p = next((i for i, b in enumerate(result) if b["type"] == "p"), None)
    if first_p and first_p > 0:
        result = [b for i, b in enumerate(result)
                  if b["type"] != "img" or i >= first_p
                  or bool(b.get("alt", ""))]

    # 2. Trim images after the last paragraph (related-article thumbnails)
    last_p = next((i for i, b in enumerate(reversed(result))
                   if b["type"] == "p"), None)
    if last_p is not None:
        cut = len(result) - last_p
        result = result[:cut]

    return result


def clean_text(t):
    """Removes extra whitespace and control characters."""
    t = re.sub(r"\[\d+\]", "", t)          # Wikipedia references [1]
    t = re.sub(r"\s+", " ", t).strip()
    return t

# ── EPUB generation ───────────────────────────────────────────────────────

CSS = """
body  { font-family: sans-serif; margin: 0.8em; line-height: 1.6; }
h1    { font-size: 1.2em; font-weight: bold; margin: 0.5em 0 0.3em 0;
        border-bottom: 1px solid #ccc; padding-bottom: 0.2em; }
h2    { font-size: 1.05em; font-weight: bold; margin: 1em 0 0.2em 0; }
h3, h4{ font-size: 0.95em; font-weight: bold; margin: 0.8em 0 0.2em 0; }
p     { margin: 0.3em 0 0.5em 0; font-size: 0.9em; }
ul    { margin: 0.2em 0 0.4em 1em; padding: 0; }
li    { font-size: 0.88em; margin: 0.15em 0; }
li::before { content: "• "; }
.meta { font-size: 0.75em; color: #888; margin-bottom: 0.8em; }
hr    { border: none; border-top: 1px solid #ddd; margin: 0.6em 0; }
.img-wrap { text-align: center; margin: 0.5em 0; }
.img-wrap img { max-width: 100%; height: auto; display: block; margin: 0 auto; }
.img-wrap .meta { text-align: center; font-style: italic; }
"""

def blocks_to_html(title, blocks, url, date_str):
    """Converts a list of blocks to the page content HTML."""
    lines = [f'<h1>{_esc(title)}</h1>',
             f'<p class="meta">{_esc(url)}<br/>{date_str}</p><hr/>']

    for b in blocks:
        btype = b["type"]
        if btype == "img":
            alt  = _esc(b.get("alt", ""))
            name = b["name"]
            lines.append(
                f'<div class="img-wrap">'
                f'<img src="images/{name}" alt="{alt}"/>'
                + (f'<p class="meta">{alt}</p>' if alt else "")
                + f'</div>'
            )
        else:
            t = _esc(b["text"])
            if btype == "h1":
                lines.append(f"<h2>{t}</h2>")
            elif btype in ("h2","h3","h4"):
                lines.append(f"<h3>{t}</h3>")
            elif btype == "p":
                lines.append(f"<p>{t}</p>")
            elif btype == "li":
                lines.append(f"<li>{t}</li>")

    return "\n".join(lines)


def _esc(t):
    return (t.replace("&","&amp;")
             .replace("<","&lt;")
             .replace(">","&gt;")
             .replace('"',"&quot;"))


def xhtml_page(title, body):
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
<head><meta charset="utf-8"/>
<title>{_esc(title)}</title>
<link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>{body}</body>
</html>"""


def generate_epub(path, title, blocks, url):
    uid      = "article-" + re.sub(r"[^\w]","",ascii_normalise(title))[:40].lower()
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Collect images from all blocks
    images = {b["name"]: b["data"]
              for b in blocks if b["type"] == "img"}

    # Split into pages of ~60 blocks (avoid overly long pages)
    page_size = 60
    pages_raw = [blocks[i:i+page_size]
                 for i in range(0, max(1, len(blocks)), page_size)]

    pages  = []
    manids = []
    navpts = []

    for i, page_blocks in enumerate(pages_raw):
        pid   = f"p{i:03d}"
        fname = f"{pid}.html"
        if i == 0:
            body = blocks_to_html(title, page_blocks, url, date_str)
        else:
            body = blocks_to_html(f"{title} ({i+1})", page_blocks, "", "")
        pages.append((pid, fname, xhtml_page(title, body)))
        manids.append((pid, fname))
        label = title if i == 0 else f"{title} — part {i+1}"
        navpts.append((f"n{i}", fname, label))

    manifest = "\n    ".join(
        f'<item id="{pid}" href="{fn}" media-type="application/xhtml+xml"/>'
        for pid, fn in manids
    )
    manifest += '\n    <item id="css" href="style.css" media-type="text/css"/>'
    manifest += '\n    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    for img_name in images:
        img_id = re.sub(r"[^\w]", "_", img_name)
        manifest += f'\n    <item id="{img_id}" href="images/{img_name}" media-type="image/png"/>'

    spine   = "\n    ".join(f'<itemref idref="{pid}"/>' for pid, _ in manids)
    nav_xml = "\n    ".join(
        f'<navPoint id="{nid}" playOrder="{i}">'
        f'<navLabel><text>{_esc(label)}</text></navLabel>'
        f'<content src="{src}"/></navPoint>'
        for i, (nid, src, label) in enumerate(navpts)
    )

    container_xml = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf"
              media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{_esc(title)}</dc:title>
    <dc:language>en</dc:language>
    <dc:identifier id="uid">{uid}</dc:identifier>
    <dc:source>{_esc(url)}</dc:source>
    <dc:date>{date.today().isoformat()}</dc:date>
  </metadata>
  <manifest>
    {manifest}
  </manifest>
  <spine toc="ncx">
    {spine}
  </spine>
</package>"""

    ncx = f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="{uid}"/></head>
  <docTitle><text>{_esc(title)}</text></docTitle>
  <navMap>
    {nav_xml}
  </navMap>
</ncx>"""

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container_xml)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/toc.ncx", ncx)
        z.writestr("OEBPS/style.css", CSS)
        for pid, fname, html in pages:
            z.writestr(f"OEBPS/{fname}", html)
        for img_name, img_data in images.items():
            z.writestr(f"OEBPS/images/{img_name}", img_data,
                       compress_type=zipfile.ZIP_STORED)

    return os.path.getsize(path) // 1024


def ascii_normalise(s):
    return unicodedata.normalize("NFD", s).encode("ascii","ignore").decode()


def build_filename(title):
    """Generates a safe filename from the article title."""
    n = ascii_normalise(title).lower()
    n = re.sub(r"[^\w\s-]", "", n)
    n = re.sub(r"[\s_-]+", "_", n).strip("_")
    return (n[:60] or "article") + ".epub"

# ── Mode: list saved articles ─────────────────────────────────────────────

def list_articles(detailed=False):
    if not os.path.exists(OUTPUT_FOLDER):
        print(f"  Folder '{OUTPUT_FOLDER}' does not exist yet.")
        return

    epubs = sorted(
        [f for f in os.listdir(OUTPUT_FOLDER) if f.endswith(".epub")],
        key=lambda f: os.path.getmtime(os.path.join(OUTPUT_FOLDER, f)),
        reverse=True
    )

    if not epubs:
        print(f"  No articles saved in '{OUTPUT_FOLDER}/'.")
        return

    print(f"\n  Articles saved in {OUTPUT_FOLDER}/  ({len(epubs)} files)\n")

    for f in epubs:
        path = os.path.join(OUTPUT_FOLDER, f)
        if detailed:
            kb    = os.path.getsize(path) // 1024
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            dt    = mtime.strftime("%Y-%m-%d %H:%M")
            print(f"  {dt}  {kb:4d} KB  {f}")
        else:
            print(f"  {f}")

    print()

# ── Main ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        prog=os.path.basename(sys.argv[0]),
        description="Saves the content of a URL as an EPUB for offline reading.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s https://en.wikipedia.org/wiki/Fernando_Pessoa\n"
            "  %(prog)s https://www.example.com/article -t \"Article Title\"\n"
            "  %(prog)s https://en.wikipedia.org/wiki/Shortwave -t \"Shortwave Radio\"\n"
            "  %(prog)s -l         # list saved articles\n"
            "  %(prog)s -l -d      # list with date and size\n"
        ),
    )
    ap.add_argument("url",
        nargs="?", metavar="URL",
        help="URL of the page to save.")
    ap.add_argument("-t","--title",
        metavar="TEXT",
        help="Custom title (default: detected from the page).")
    ap.add_argument("-o","--output",
        metavar="FILE",
        help="Output path (default: articles/<title>.epub).")
    ap.add_argument("-l","--list",
        action="store_true",
        help="List saved articles.")
    ap.add_argument("-d","--detailed",
        action="store_true",
        help="With -l: show date and size.")
    ap.add_argument("--no-images",
        action="store_true",
        help="Do not download images (faster, smaller file).")

    args = ap.parse_args()

    if args.list:
        list_articles(args.detailed)
        sys.exit(0)

    if not args.url:
        ap.print_help()
        sys.exit(1)

    url = args.url
    print(f"\n  Fetching: {url}")

    # Download and parse
    soup   = fetch_page(url)
    title  = args.title or extract_title(soup, url)
    blocks = extract_content(soup, include_images=not args.no_images)

    n_pars = sum(1 for b in blocks if b["type"] == "p")
    n_imgs = sum(1 for b in blocks if b["type"] == "img")
    print(f"  Title   : {title}")
    print(f"  Blocks  : {len(blocks)} ({n_pars} paragraphs"
          + (f", {n_imgs} images" if n_imgs else "") + ")")

    if not blocks:
        print("  ✗ Could not extract content from this page.")
        print("  Try a manual CSS selector or check if the page requires JS.")
        sys.exit(1)

    # Determine output path
    if args.output:
        path = args.output
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    else:
        os.makedirs(OUTPUT_FOLDER, exist_ok=True)
        path = os.path.join(OUTPUT_FOLDER, build_filename(title))

    # Check if file already exists
    if os.path.exists(path):
        resp = input(f"  '{path}' already exists. Overwrite? [y/N] ").strip().lower()
        if resp != "y":
            print("  Cancelled.")
            sys.exit(0)

    # Generate EPUB
    kb = generate_epub(path, title, blocks, url)
    print(f"  ✓ Saved: {path}  ({kb} KB)\n")
