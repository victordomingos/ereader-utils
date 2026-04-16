#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
get-news.py

Downloads RSS feeds and generates EPUBs optimised for the xteink x4
(CrossPoint Reader, 480×800px, 220ppi, no touch).

Reads the same feed_config.lua used by KOReader — no reconfiguration needed.

Output structure:
  news/
    2026-04-16/
      portugal-news/
        rtp.epub
      apple-mac/
        appleinsider-news.epub

Usage:
  python3 get-news.py
  python3 get-news.py -c ~/koreader/feed_config.lua
  python3 get-news.py --only RTP
  python3 get-news.py --list
  python3 get-news.py --clean
"""

import re, sys, time, zipfile, unicodedata, html, io, json, sqlite3, base64, hashlib, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

# ── Install dependencies ───────────────────────────────────────────────────
_DEPS = {
    "requests":       "requests",
    "feedparser":     "feedparser",
    "beautifulsoup4": "bs4",
    "lxml":           "lxml",
    "Pillow":         "PIL",
}

def _install():
    """Install missing dependencies. Runs in main process only."""
    import subprocess
    for pkg, module in _DEPS.items():
        try:
            __import__(module)
        except ImportError:
            print(f"  Installing {pkg}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install",
                                   pkg, "-q"])

if __name__ == "__main__" or "pytest" in sys.modules:
    _install()

import requests
import feedparser
from bs4 import BeautifulSoup
from PIL import Image

# ── Image dimensions ───────────────────────────────────────────────────────
IMG_MAX_W = 440
IMG_MAX_H = 700

# ── Filters ────────────────────────────────────────────────────────────────
_AD_DOMAINS = re.compile(
    r"doubleclick\.net|googlesyndication|adservice|googleads|"
    r"amazon-adsystem|adsrvr\.org|adnxs\.com|criteo\.|rubiconproject|"
    r"pubmatic\.com|openx\.net|moatads|scorecardresearch|"
    r"quantserve|omtrdc\.net|pixel\.|tracking\.|analytics\.|"
    r"beacon\.|/ads?/|/advert|/banner|/promo/|/sponsor",
    re.IGNORECASE
)

_DECORATIVE_ALT = re.compile(
    r"feature\s+desaturat|"
    r"\bgeneric\b|\bstock\s+image\b|"
    r"\bplaceholder\b|"
    r"\bheader[\s-]image\b|\bcover[\s-]image\b",
    re.IGNORECASE
)

_PROMO_TITLE = re.compile(
    r"\b(deal|deals|sale|discount|save\s+\$|\d+%\s*off|\$\d+\s+off"
    r"|price\s+(drop|cut|war|match)|best\s+price|lowest\s+price"
    r"|record[\s-]low\s+price|limited.time|coupon"
    r"|how\s+to\s+(get|save|score)\s+\d+%"
    r"|dips?\s+to\s+\$|drops?\s+to\s+\$|back\s+to\s+\$"
    r"|is\s+back\s+on\s+sale|on\s+sale\b)\b",
    re.IGNORECASE
)

# ── Configuration ──────────────────────────────────────────────────────────
OUTPUT_DIR      = Path("news")
DEFAULT_CONFIG  = Path("feed_config.lua")
TIMEOUT         = 15
ARTICLE_WORKERS = 3
UA = {"User-Agent": "XteinkNews/1.0 (epub e-reader; python-requests)"}

UA_LINKS = [
    ("User-Agent",      "Links (2.30; Darwin 25.5.0 x86_64; LLVM/Clang 15.0; text)"),
    ("Accept",          "*/*"),
    ("Accept-Language", "pt,en;q=0.2,*;q=0.1"),
    ("Accept-Encoding", "gzip, deflate, bzip2"),
    ("Accept-Charset",  "us-ascii,ISO-8859-1,utf-8"),
    ("Connection",      "keep-alive"),
]

# ── Per-domain rate limiter ────────────────────────────────────────────────
_dom_semaphores: dict[str, threading.Semaphore] = {}
_dom_lock = threading.Lock()

def _domain_semaphore(url: str, max_concurrent: int = 2) -> threading.Semaphore:
    try:
        domain = urlparse(url).netloc
    except Exception:
        domain = url
    with _dom_lock:
        if domain not in _dom_semaphores:
            _dom_semaphores[domain] = threading.Semaphore(max_concurrent)
        return _dom_semaphores[domain]

# ── SQLite cache ───────────────────────────────────────────────────────────

class ArticleCache:
    """
    Thread-safe persistent SQLite cache.
    Each thread has its own connection (thread-local).
    WAL mode for concurrent reads. Lock only on writes.
    Tables: articles, feeds, epubs.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS articles (
        url    TEXT PRIMARY KEY,
        blocks TEXT NOT NULL,
        ts     INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS feeds (
        url    TEXT PRIMARY KEY,
        data   TEXT NOT NULL,
        ts     INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS epubs (
        hash   TEXT PRIMARY KEY,
        data   BLOB NOT NULL,
        ts     INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_art_ts  ON articles(ts);
    CREATE INDEX IF NOT EXISTS idx_feed_ts ON feeds(ts);
    CREATE INDEX IF NOT EXISTS idx_epub_ts ON epubs(ts);
    """

    def __init__(self, path: Path):
        self._db    = str(path)
        self._lock  = threading.Lock()
        self._local = threading.local()
        with self._lock:
            self._cx().executescript(self._SCHEMA)

    def _cx(self) -> sqlite3.Connection:
        if not getattr(self._local, "cx", None):
            cx = sqlite3.connect(self._db, timeout=30)
            cx.execute("PRAGMA journal_mode=WAL")
            cx.execute("PRAGMA synchronous=NORMAL")
            self._local.cx = cx
        return self._local.cx

    # ── Articles ──────────────────────────────────────────────────────────

    def get(self, url: str, max_days: int = 30) -> list[dict] | None:
        limit_ts = int(time.time()) - max_days * 86400
        try:
            row = self._cx().execute(
                "SELECT blocks FROM articles WHERE url=? AND ts>=?",
                (url, limit_ts)
            ).fetchone()
            if not row or not row[0]:
                return None
            return self._deserialize(row[0])
        except Exception:
            return None

    def save(self, url: str, blocks: list[dict]) -> None:
        try:
            data = self._serialize(blocks)
            with self._lock:
                cx = self._cx()
                cx.execute(
                    "INSERT OR REPLACE INTO articles(url,blocks,ts) VALUES(?,?,?)",
                    (url, data, int(time.time()))
                )
                cx.commit()
        except Exception:
            pass

    # ── Feeds ─────────────────────────────────────────────────────────────

    def get_feed(self, url: str, max_min: int = 60):
        import pickle
        limit_ts = int(time.time()) - max_min * 60
        try:
            row = self._cx().execute(
                "SELECT data FROM feeds WHERE url=? AND ts>=?",
                (url, limit_ts)
            ).fetchone()
            if not row or not row[0]:
                return None
            return pickle.loads(base64.b64decode(row[0]))
        except Exception:
            return None

    def save_feed(self, url: str, feed_obj) -> None:
        import pickle
        try:
            data = base64.b64encode(pickle.dumps(feed_obj)).decode()
            with self._lock:
                cx = self._cx()
                cx.execute(
                    "INSERT OR REPLACE INTO feeds(url,data,ts) VALUES(?,?,?)",
                    (url, data, int(time.time()))
                )
                cx.commit()
        except Exception:
            pass

    # ── EPUBs ─────────────────────────────────────────────────────────────

    def get_epub(self, content_hash: str) -> bytes | None:
        try:
            row = self._cx().execute(
                "SELECT data FROM epubs WHERE hash=?", (content_hash,)
            ).fetchone()
            return bytes(row[0]) if row else None
        except Exception:
            return None

    def save_epub(self, content_hash: str, epub_bytes: bytes) -> None:
        try:
            with self._lock:
                cx = self._cx()
                cx.execute(
                    "INSERT OR REPLACE INTO epubs(hash,data,ts) VALUES(?,?,?)",
                    (content_hash, epub_bytes, int(time.time()))
                )
                cx.commit()
        except Exception:
            pass

    # ── Cleanup ───────────────────────────────────────────────────────────

    def clean(self, max_days: int) -> int:
        t = int(time.time())
        try:
            with self._lock:
                cx = self._cx()
                n  = cx.execute("DELETE FROM articles WHERE ts<?",
                                (t - max_days * 86400,)).rowcount
                n += cx.execute("DELETE FROM feeds WHERE ts<?",
                                (t - 2 * 86400,)).rowcount
                n += cx.execute("DELETE FROM epubs WHERE ts<?",
                                (t - max_days * 86400,)).rowcount
                cx.commit()
                cx.execute("VACUUM")
            return n
        except Exception:
            return 0

    # ── Serialisation ─────────────────────────────────────────────────────

    @staticmethod
    def _serialize(blocks: list[dict]) -> str:
        def conv(b):
            if b.get("t") == "img" and isinstance(b.get("data"), bytes):
                return {**b, "data": base64.b64encode(b["data"]).decode()}
            return b
        return json.dumps([conv(b) for b in blocks], ensure_ascii=False)

    @staticmethod
    def _deserialize(text: str) -> list[dict]:
        def conv(b):
            if b.get("t") == "img" and isinstance(b.get("data"), str):
                return {**b, "data": base64.b64decode(b["data"])}
            return b
        return [conv(b) for b in json.loads(text)]


# ── HTTP fetch ─────────────────────────────────────────────────────────────

def _html_valid(s: str) -> bool:
    if len(s) < 500:
        return False
    h = s.lower()
    if len(s) < 20_000:
        if "just a moment" in h and "enable javascript" in h:
            return False
        if "checking your browser" in h:
            return False
    return True


def fetch_html(url: str, timeout: int = TIMEOUT) -> str | None:
    """
    Fetch HTML/XML with progressive fallback:
    0. urllib + links UA  (HTTP/1.1, bypasses AppleInsider/Cloudflare)
    1. requests Chrome UA
    2. requests links UA
    3. curl + links UA
    4. links -source      (native TLS)
    """
    import subprocess, urllib.request, gzip as _gz

    try:
        req = urllib.request.Request(url, headers=dict(UA_LINKS))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if "gzip" in resp.headers.get("Content-Encoding", ""):
                raw = _gz.decompress(raw)
            s = raw.decode("utf-8", errors="replace")
            if _html_valid(s):
                return s
    except Exception:
        pass

    for hdrs in [UA, dict(UA_LINKS)]:
        try:
            r = requests.get(url, headers=hdrs, timeout=timeout)
            if r.status_code == 200 and _html_valid(r.text):
                return r.text
        except Exception:
            pass

    cmd = ["curl", "-s", "--max-time", str(timeout), "--compressed",
           "--location"]
    for k, v in UA_LINKS:
        cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
        s = res.stdout.decode("utf-8", errors="replace")
        if res.returncode == 0 and _html_valid(s):
            return s
    except Exception:
        pass

    try:
        res = subprocess.run(["links", "-source", url],
                             capture_output=True, timeout=timeout + 5)
        s = res.stdout.decode("utf-8", errors="replace")
        if res.returncode == 0 and _html_valid(s):
            return s
    except Exception:
        pass

    return None


def fetch_feed(url: str):
    """Fetch and parse RSS/Atom with progressive fallback including links -source."""
    import subprocess, urllib.request, gzip as _gz

    def _try(content: str):
        r = feedparser.parse(content)
        return r if (r.entries or not r.bozo) else None

    try:
        req = urllib.request.Request(url, headers=dict(UA_LINKS))
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            if "gzip" in resp.headers.get("Content-Encoding", ""):
                raw = _gz.decompress(raw)
            r = _try(raw.decode("utf-8", errors="replace"))
            if r:
                return r
    except Exception:
        pass

    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        if r.status_code == 200:
            result = _try(r.text)
            if result:
                return result
    except Exception:
        pass

    try:
        res = subprocess.run(["links", "-source", url],
                             capture_output=True, timeout=TIMEOUT + 5)
        if res.returncode == 0 and res.stdout:
            r = _try(res.stdout.decode("utf-8", errors="replace"))
            if r:
                return r
    except Exception:
        pass

    return feedparser.parse(url, request_headers=dict(UA_LINKS))


# ── CSS selectors ──────────────────────────────────────────────────────────

REMOVE_SELECTORS = [
    "nav", "header", "footer", "aside", "script", "style",
    "noscript", "iframe", "form", "button",
    ".related-articles", ".recommended", ".comments", "#comments",
    ".social-share", ".share-buttons",
    ".ad", ".ads", ".cookie-notice", ".gdpr",
    ".advertisement", ".adsbygoogle", "[data-ad]", "[id*='google_ads']",
    "[class*='sponsored']", "[class*='promo']",
    "[role='navigation']", "[role='banner']", "[role='contentinfo']",
    "[aria-hidden='true']",
    ".side-list", ".discussion", ".roundup-index",
    ".post-actions", ".stories-list", ".forum-thread-list",
    ".article-actions", ".article-tags", ".article-footer",
    ".buyer-guide-rail", ".buyersguide-widget",
    ".article-related", ".article-bottom-links", ".related-content",
    ".related-links", ".bottom-links",
    "[class*='related']", "[class*='recommend']",
    "[class*='more-stories']", "[class*='also-read']",
    "[class*='see-also']", "[class*='read-more']",
    "[id*='related']", "[id*='recommend']",
]

CONTENT_SELECTORS = [
    "article", "main", "[role='main']",
    ".article-body", ".article__body", ".story-body",
    ".post-content", ".entry-content", ".content-body",
    ".articleBody",
    ".single-article-content",
    "#content", "#main-content", ".main-content",
]

# ── Image source extraction ────────────────────────────────────────────────

def _img_src(img_el) -> str:
    """Extract real image URL covering common lazy-loading patterns."""
    candidates = [
        img_el.get("data-src"),
        img_el.get("data-lazy-src"),
        img_el.get("data-original"),
        img_el.get("data-full-src"),
        img_el.get("data-img-src"),
    ]
    for attr in ("srcset", "data-srcset", "data-lazy-srcset"):
        val = img_el.get(attr, "")
        if val:
            entries = [e.strip().split() for e in val.split(",") if e.strip()]
            def _w(e):
                try:
                    return int(e[1].rstrip("wx")) if len(e) > 1 else 0
                except Exception:
                    return 0
            entries.sort(key=_w, reverse=True)
            if entries and entries[0]:
                candidates.append(entries[0][0])
            break
    candidates.append(img_el.get("src", ""))
    for src in candidates:
        if not src or src.startswith("data:"):
            continue
        if src.startswith("//"):
            return "https:" + src
        if src.startswith("http"):
            return src
    return ""


def process_image(url: str,
                  max_size: tuple[int, int] | None = None) -> bytes | None:
    """Download, convert to greyscale PNG, resize. Rejects ads and banners."""
    max_w, max_h = max_size or (IMG_MAX_W, IMG_MAX_H)
    if _AD_DOMAINS.search(url):
        return None
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT, stream=True)
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
        if img.width > max_w or img.height > max_h:
            img.thumbnail((max_w, max_h), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        return None


# ── Article download ───────────────────────────────────────────────────────

def download_article(url: str, cfg: dict,
                     verbose: bool = False,
                     cache: ArticleCache | None = None,
                     rss_title: str = "") -> list[dict] | None:
    """
    Download full article, extract text+image blocks.
    Cache-first. Falls back to links -dump for Cloudflare sites.
    """
    if cache:
        cached = cache.get(url)
        if cached is not None:
            return cached

    html_text = fetch_html(url)
    if not html_text:
        if verbose:
            print(f"\n      ✗ download failed")
        return None

    soup = BeautifulSoup(html_text, "lxml")

    for sel in REMOVE_SELECTORS:
        for el in (soup.select(sel) if sel.startswith((".", "#", "["))
                   else soup.find_all(sel)):
            el.decompose()

    if cfg["enable_filter"] and cfg["filter_element"]:
        container = soup.select_one(cfg["filter_element"])
    else:
        container = None
        for sel in CONTENT_SELECTORS:
            el = soup.select_one(sel)
            if el and len(el.get_text(strip=True)) > 200:
                container = el
                break

    if not container:
        container = soup.find("body") or soup

    if cfg["block_element"]:
        for el in container.select(cfg["block_element"]):
            el.decompose()

    include_images = cfg.get("include_images", False)
    blocks: list[dict] = []
    visited: set = set()
    img_count = [0]

    def extract(el):
        if id(el) in visited:
            return
        visited.add(id(el))
        tag = getattr(el, "name", None)
        if not tag:
            return
        match tag:
            case "h1" | "h2" | "h3":
                t = el.get_text(" ", strip=True)
                if t and len(t) < 200:
                    blocks.append({"t": tag, "x": clean_text(t)})
            case "p":
                if include_images:
                    for img_el in el.find_all("img"):
                        _extract_img(img_el)
                t = el.get_text(" ", strip=True)
                if t and len(t) > 30:
                    blocks.append({"t": "p", "x": clean_text(t)})
            case "img":
                if include_images:
                    _extract_img(el)
            case "figure":
                if include_images:
                    for img_el in el.find_all("img"):
                        _extract_img(img_el)
                cap = el.find("figcaption")
                if cap:
                    t = cap.get_text(" ", strip=True)
                    if t:
                        blocks.append({"t": "p", "x": clean_text(t)})
            case "ul" | "ol":
                for li in el.find_all("li", recursive=False):
                    t = li.get_text(" ", strip=True)
                    if t and len(t) > 10:
                        blocks.append({"t": "li", "x": clean_text(t)})
            case "div" | "section" | "article" | "main":
                for child in el.children:
                    if hasattr(child, "name"):
                        extract(child)

    def _extract_img(img_el):
        src = _img_src(img_el)
        if not src:
            return
        alt = clean_text(img_el.get("alt", ""))
        if alt and _DECORATIVE_ALT.search(alt):
            return
        data = process_image(src)
        if data:
            img_count[0] += 1
            name = f"img{img_count[0]:04d}.png"
            blocks.append({"t": "img", "src": src, "alt": alt,
                           "name": name, "data": data})

    extract(container)

    # Deduplicate consecutive text blocks
    result: list[dict] = []
    last_text = None
    for b in blocks:
        if b["t"] == "img":
            result.append(b)
            last_text = None
        elif b.get("x") != last_text:
            result.append(b)
            last_text = b.get("x")

    # Remove empty-alt images before first paragraph (hero/decorative)
    first_p = next((i for i, b in enumerate(result) if b["t"] == "p"), None)
    if first_p and first_p > 0:
        result = [b for i, b in enumerate(result)
                  if b["t"] != "img" or i >= first_p
                  or bool(b.get("alt", ""))]

    # Remove images after last paragraph (related thumbnails)
    last_p = next((i for i, b in enumerate(reversed(result))
                   if b["t"] == "p"), None)
    if last_p is not None:
        cut = len(result) - last_p
        result = result[:cut]

    # Cloudflare JS embedding: use links -dump when HTML has too little text
    n_pars = sum(1 for b in result if b["t"] == "p")
    if (len(html_text) > 50_000
            and ("cf-" in html_text or "cloudflare" in html_text.lower())
            and n_pars < 8):
        dump = _extract_links_dump(url, rss_title=rss_title,
                                   html_raw=html_text,
                                   include_images=include_images, cfg=cfg)
        if dump and sum(1 for b in dump if b["t"] == "p") > n_pars:
            result = dump

    if verbose:
        np = sum(1 for b in result if b["t"] == "p")
        ni = sum(1 for b in result if b["t"] == "img")
        print(f"      blocks: {len(result)} ({np} paragraphs, {ni} images)"
              f"{'  ✗ empty' if not result else ''}")

    if result and cache:
        cache.save(url, result)

    return result or None


def _extract_links_dump(url: str,
                        rss_title: str = "",
                        html_raw: str = "",
                        include_images: bool = False,
                        cfg: dict | None = None) -> list[dict] | None:
    """
    Use 'links -dump' for rendered text (resolves Cloudflare JS embedding).
    Headings extracted from raw HTML for proper formatting.
    Images extracted from raw HTML with correct ordering:
      - Article images: first one inserted after first paragraph, rest appended
      - Avatar/author images: always appended at the very end
    """
    import subprocess

    # Extract real headings from HTML (present even in Cloudflare pages)
    html_headings: dict[str, str] = {}
    if html_raw:
        soup_h = BeautifulSoup(html_raw, "lxml")
        for tag in ("h2", "h3", "h4"):
            for el in soup_h.find_all(tag):
                t = el.get_text(" ", strip=True)
                if t and 3 < len(t) < 150:
                    html_headings[re.sub(r"\s+", " ", t.lower())] = tag

    try:
        res = subprocess.run(["links", "-dump", url],
                             capture_output=True, timeout=30)
        if res.returncode != 0:
            return None
        dump = res.stdout.decode("utf-8", errors="replace")
        if len(dump) < 200:
            return None
    except Exception:
        return None

    lines = dump.splitlines()

    # Locate article start by RSS title keywords
    start = 0
    if rss_title:
        words = [p.lower() for p in rss_title.split() if len(p) > 3][:5]
        for i, line in enumerate(lines):
            if sum(1 for w in words if w in line.lower()) >= min(2, len(words)):
                start = i
                break
        if start == 0 and words:
            for i, line in enumerate(lines):
                if any(w in line.lower() for w in words):
                    start = i
                    break

    _STOP = re.compile(
        r"^(related|more from|more ways to|you may|also read|see also|"
        r"most popular|tags:|share:|comments?:|newsletter|subscribe|"
        r"follow us|copyright|all rights|privacy|terms of|"
        r"discuss this|leave a (reply|comment)|post a comment|"
        r"article originally|first appeared on)",
        re.IGNORECASE
    )
    end = len(lines)
    for i in range(start, len(lines)):
        if _STOP.match(lines[i].strip()):
            end = i
            break

    lines = lines[start:end]
    blocks: list[dict] = []
    current: list[str] = []

    def flush():
        if not current:
            return
        text = " ".join(current).strip()
        current.clear()
        if not text or len(text) < 15:
            return
        if re.match(r"^\[[\d]+\]$|^https?://", text):
            return
        norm = re.sub(r"\s+", " ", text.lower())
        heading_tag = next((h_tag for h_norm, h_tag in html_headings.items()
                            if norm == h_norm or h_norm in norm), None)
        if heading_tag:
            blocks.append({"t": heading_tag, "x": clean_text(text)})
        elif not blocks and len(text) < 250:
            blocks.append({"t": "h1", "x": clean_text(text)})
        else:
            blocks.append({"t": "p", "x": clean_text(text)})

    for line in lines:
        s = line.strip()
        if not s:
            flush()
        elif re.match(r"^\*\s|^\[\d+\]|^━+$|^─+$|^={3,}$", s):
            flush()
        else:
            current.append(s)
    flush()

    # Retry without start offset if too few paragraphs found
    n_pars = sum(1 for b in blocks if b["t"] == "p")
    if n_pars < 2 and start > 0:
        blocks, current = [], []
        for line in dump.splitlines()[:end]:
            s = line.strip()
            if not s:
                flush()
            elif re.match(r"^\*\s|^\[\d+\]|^━+$|^─+$|^={3,}$", s):
                flush()
            else:
                current.append(s)
        flush()
        n_pars = sum(1 for b in blocks if b["t"] == "p")

    # Extract images from raw HTML
    if include_images and html_raw and cfg:
        soup_img = BeautifulSoup(html_raw, "lxml")

        for sel in ["script", "style", "[aria-hidden='true']",
                    ".article-related", ".article-bottom-links",
                    "[class*='newsletter']", "[class*='subscribe']"]:
            for el in (soup_img.select(sel) if sel.startswith(("[", ".", "#"))
                       else soup_img.find_all(sel)):
                el.decompose()

        container_img = None
        if cfg and cfg.get("enable_filter") and cfg.get("filter_element"):
            container_img = soup_img.select_one(cfg["filter_element"])
        if not container_img:
            for sel in CONTENT_SELECTORS:
                el = soup_img.select_one(sel)
                if el and len(el.get_text(strip=True)) > 200:
                    container_img = el
                    break
        if not container_img:
            container_img = soup_img.find("body") or soup_img

        img_count    = [0]
        avatars      = []   # author photos — appended at end
        article_imgs = [0]  # count of non-avatar images found

        for img_el in container_img.find_all("img"):
            src = _img_src(img_el)
            if not src:
                continue
            alt   = clean_text(img_el.get("alt", ""))
            alt_l = alt.lower()
            src_l = src.lower()

            if alt and _DECORATIVE_ALT.search(alt):
                continue
            if any(p in alt_l for p in ("on apple news", "preferred source",
                                        "app store", " logo")):
                continue
            if any(p in src_l for p in ("/assets/", "/badge", ".svg")):
                continue
            if any(getattr(p, "name", "") == "aside" for p in img_el.parents):
                continue

            # Detect author avatar — by alt text, src pattern, OR parent container.
            # AppleInsider puts author photo in div.article-aux.
            # Generic sites use byline, author-bio, contributor containers.
            parent_classes = " ".join(
                c for p in img_el.parents
                if hasattr(p, "get")
                for c in p.get("class", [])
            ).lower()
            is_avatar = (
                any(p in alt_l for p in ("profile picture", "avatar"))
                or any(p in src_l for p in ("/avatar", "/profile", "profile-pic"))
                or any(p in parent_classes for p in
                       ("article-aux", "byline", "bio", "contributor",
                        "journalist", "writer", "post-meta", "article-meta",
                        "author-info", "author-block", "author-card"))
            )

            data = process_image(src, max_size=(80, 80) if is_avatar else None)
            if not data:
                continue

            img_count[0] += 1
            name = f"img{img_count[0]:04d}.png"
            blk  = {"t": "img", "src": src, "alt": alt, "name": name, "data": data}

            if is_avatar:
                avatars.append(blk)
            else:
                article_imgs[0] += 1
                if article_imgs[0] == 1 and len(blocks) >= 2:
                    blocks.insert(2, blk)  # after first paragraph
                else:
                    blocks.append(blk)

            if img_count[0] >= 4:
                break

        # Avatars at end — deduplicated by src (same photo appears twice in some sites)
        seen_avatar_srcs: set[str] = set()
        for blk in avatars:
            if blk["src"] not in seen_avatar_srcs:
                seen_avatar_srcs.add(blk["src"])
                blocks.append(blk)

    n_pars = sum(1 for b in blocks if b["t"] == "p")
    return blocks if n_pars >= 2 else None


def blocks_from_entry(entry, include_images: bool = False) -> list[dict] | None:
    """Extract blocks from RSS feed content (content:encoded / summary)."""
    html_content = ""
    if hasattr(entry, "content") and entry.content:
        html_content = entry.content[0].get("value", "")
    if not html_content:
        html_content = getattr(entry, "summary", "") or ""
    if not html_content or len(html_content) < 200:
        return None

    soup   = BeautifulSoup(html_content, "lxml")
    blocks: list[dict] = []
    img_count = [0]

    def extract(el):
        tag = getattr(el, "name", None)
        if not tag:
            return
        match tag:
            case "h1" | "h2" | "h3":
                t = el.get_text(" ", strip=True)
                if t and len(t) < 200:
                    blocks.append({"t": tag, "x": clean_text(t)})
            case "p":
                if include_images:
                    for img_el in el.find_all("img"):
                        _simple_img(img_el)
                t = el.get_text(" ", strip=True)
                if t and len(t) > 30:
                    blocks.append({"t": "p", "x": clean_text(t)})
            case "img":
                if include_images:
                    _simple_img(el)
            case "ul" | "ol":
                for li in el.find_all("li", recursive=False):
                    t = li.get_text(" ", strip=True)
                    if t and len(t) > 10:
                        blocks.append({"t": "li", "x": t})
            case _:
                for child in el.children:
                    if hasattr(child, "name"):
                        extract(child)

    def _simple_img(img_el):
        src = _img_src(img_el)
        if not src:
            return
        alt = clean_text(img_el.get("alt", ""))
        if alt and _DECORATIVE_ALT.search(alt):
            return
        data = process_image(src)
        if data:
            img_count[0] += 1
            name = f"img{img_count[0]:04d}.png"
            blocks.append({"t": "img", "src": src, "alt": alt,
                           "name": name, "data": data})

    body = soup.find("body")
    for el in (body.children if body else [soup]):
        if hasattr(el, "name"):
            extract(el)

    # Remove images before first paragraph
    first_p = next((i for i, b in enumerate(blocks) if b["t"] == "p"), None)
    if first_p is not None and first_p > 0:
        blocks = [b for i, b in enumerate(blocks)
                  if b["t"] != "img" or i >= first_p]

    n_pars = sum(1 for b in blocks if b["t"] == "p")
    return blocks if n_pars >= 2 else None


# ── Text utilities ─────────────────────────────────────────────────────────

def clean_text(t: str) -> str:
    t = re.sub(r"\[\d+\]", "", t)
    t = html.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def clean_html(t: str) -> str:
    t = re.sub(r"<[^>]+>", " ", t or "")
    t = html.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def ascii_norm(s: str) -> str:
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode()


def safe_filename(name: str) -> str:
    n = ascii_norm(name).lower()
    n = re.sub(r"[^\w\s-]", "", n)
    n = re.sub(r"[\s_-]+", "-", n).strip("-")
    return n[:50] or "feed"


def short_feed_name(name: str) -> str:
    for sep in (":", "|", "-", ",", "–"):
        if sep in name:
            name = name.split(sep)[0].strip()
            break
    name = re.sub(
        r"\s*(all\s+stories?|rss|news|feed|blog|podcast|latest|-\s*blog)$",
        "", name, flags=re.IGNORECASE
    ).strip()
    return safe_filename(name)[:30] or "feed"


def feed_display_name(feed_parsed, url: str) -> str:
    if feed_parsed.feed.get("title"):
        return feed_parsed.feed.title.strip()
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else url


def format_pub_date(entry) -> str:
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6]).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
    return ""


# ── EPUB generation ────────────────────────────────────────────────────────

CSS = """
body  { font-family: sans-serif; margin: 0.8em; line-height: 1.6; }
h1    { font-size: 1.1em; font-weight: bold; margin: 0.5em 0 0.2em 0;
        border-bottom: 1px solid #bbb; padding-bottom: 0.15em; }
h2    { font-size: 0.95em; font-weight: bold; margin: 0.9em 0 0.2em 0; }
h3    { font-size: 0.88em; font-weight: bold; margin: 0.7em 0 0.15em 0; }
p     { margin: 0.25em 0 0.4em 0; font-size: 0.88em; }
li    { font-size: 0.85em; margin: 0.1em 0; }
li::before { content: "- "; }
.meta { font-size: 0.72em; color: #999; margin-bottom: 0.5em; }
.summary { font-size: 0.88em; font-style: italic; margin: 0.3em 0 0.5em 0; }
hr    { border: none; border-top: 1px solid #eee; margin: 0.4em 0; }
.img-wrap { text-align: center; margin: 0.5em 0; }
.img-wrap img { max-width: 100%; height: auto; display: block; margin: 0 auto; }
.img-wrap .meta { text-align: center; font-style: italic; }
"""


def _esc(t: str) -> str:
    return (t.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _xhtml(title: str, body: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
<head><meta charset="utf-8"/>
<title>{_esc(title)}</title>
<link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>{body}</body>
</html>"""


def _hash_articles(articles: list) -> str:
    h = hashlib.sha256()
    for title, url, dp, summary, blocks in articles:
        h.update(title.encode())
        h.update(url.encode())
        if blocks:
            for b in blocks:
                if b["t"] != "img":
                    h.update(b.get("x", "").encode())
                else:
                    h.update(b.get("src", b.get("name", "")).encode())
    return h.hexdigest()


def generate_epub(path: Path, feed_name: str, articles: list,
                  date_str: str, cache: ArticleCache | None = None) -> int:
    """
    Generate EPUB — one XHTML per article (real NCX chapter).
    Uses content hash to skip regeneration if content unchanged.
    """
    if cache:
        content_hash = _hash_articles(articles)
        cached = cache.get_epub(content_hash)
        if cached:
            path.write_bytes(cached)
            return len(cached) // 1024

    uid   = "news-" + re.sub(r"[^\w]", "", ascii_norm(feed_name))[:30].lower()
    title = f"{feed_name} — {date_str}"
    pages:  list[tuple] = []
    manids: list[tuple] = []
    navpts: list[tuple] = []
    images: dict[str, bytes] = {}

    # Index page
    idx = (f"<h1>{_esc(feed_name)}</h1><hr/>"
           f"<p class='meta'>{len(articles)} articles · {date_str}</p><hr/>\n")
    for t, u, dp, _, _ in articles:
        idx += f"<p>{_esc(t)}</p>\n"
        if dp:
            idx += f"<p class='meta'>{_esc(dp)}</p>\n"
    pages.append(("i0", "i.html", _xhtml(title, idx)))
    manids.append(("i0", "i.html"))
    navpts.append(("n0", "i.html", "Index"))

    for art_idx, (art_title, url, dp, summary, blocks) in enumerate(articles):
        pid   = f"a{art_idx:03d}"
        fname = f"{pid}.html"
        body  = f"<h1>{_esc(art_title)}</h1>\n"
        if dp:
            body += f"<p class='meta'>{_esc(dp)}</p>\n"
        if blocks:
            for b in blocks:
                match b["t"]:
                    case "h1": body += f"<h2>{_esc(b['x'])}</h2>\n"
                    case "h2": body += f"<h2>{_esc(b['x'])}</h2>\n"
                    case "h3": body += f"<h3>{_esc(b['x'])}</h3>\n"
                    case "p":  body += f"<p>{_esc(b['x'])}</p>\n"
                    case "li": body += f"<li>{_esc(b['x'])}</li>\n"
                    case "img":
                        img_name = f"a{art_idx:03d}_{b['name']}"
                        alt = _esc(b.get("alt", ""))
                        images[img_name] = b["data"]
                        body += (
                            f'<div class="img-wrap">'
                            f'<img src="images/{img_name}" alt="{alt}"/>'
                            + (f'<p class="meta">{alt}</p>' if alt else "")
                            + f'</div>\n'
                        )
        elif summary:
            body += f"<p class='summary'>{_esc(summary)}</p>\n"
            body += f"<p class='meta'>Source: {_esc(url)}</p>\n"
        else:
            body += f"<p class='meta'>No content available.</p>\n"
        pages.append((pid, fname, _xhtml(art_title, body)))
        manids.append((pid, fname))
        navpts.append((f"n{art_idx + 1}", fname, art_title))

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
    <dc:date>{date.today().isoformat()}</dc:date>
  </metadata>
  <manifest>{manifest}</manifest>
  <spine toc="ncx">{spine}</spine>
</package>"""

    ncx = f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="{uid}"/></head>
  <docTitle><text>{_esc(title)}</text></docTitle>
  <navMap>{nav_xml}</navMap>
</ncx>"""

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container_xml)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/toc.ncx", ncx)
        z.writestr("OEBPS/style.css", CSS)
        for pid, fname, content in pages:
            z.writestr(f"OEBPS/{fname}", content)
        for img_name, img_data in images.items():
            z.writestr(f"OEBPS/images/{img_name}", img_data,
                       compress_type=zipfile.ZIP_STORED)

    epub_bytes = path.read_bytes()
    if cache:
        cache.save_epub(content_hash, epub_bytes)

    return len(epub_bytes) // 1024


# ── Config parser ──────────────────────────────────────────────────────────

def parse_config(path: Path) -> list[dict]:
    """
    Read KOReader feed_config.lua and return feed list.
    Ignores commented lines. Detects category names from comment blocks.
    """
    if not path.exists():
        print(f"  ✗ Config not found: {path}")
        sys.exit(1)

    _DOC = {"use", "set", "means", "default", "remember", "comment",
            "optional", "example", "details", "syntax", "change",
            "located", "download", "filter", "block", "credential",
            "include", "limit"}

    text    = path.read_text(encoding="utf-8")
    feeds   = []
    cur_cat = "General"

    for line in text.splitlines():
        ls = line.strip()
        if ls.startswith("--"):
            tc = re.sub(r"^-+\s*", "", ls)
            tc = re.sub(r"[=\-]+$", "", tc).strip()
            tse = re.sub(
                r"[\U00010000-\U0010ffff\U00002600-\U000027BF"
                r"\U0001F300-\U0001F9FF]+", "", tc
            ).strip()
            if (tse and 2 < len(tse) < 60
                    and not tc.startswith("=")
                    and not tse.rstrip().endswith(":")
                    and "/" not in tse.split()[-1]
                    and not any(w.lower() in _DOC for w in tse.split()[:3])):
                cat = re.sub(r"[\u200d\u200b\ufe0f]", "", tse)
                cat = re.sub(r"\s+", " ", cat).strip("/ ").strip()
                if cat:
                    cur_cat = cat
            continue

        m = re.match(r'\{\s*"(https?://[^"]+)"', ls)
        if not m:
            continue
        url = m.group(1)

        def opt_bool(key, default=False):
            m2 = re.search(rf"{key}\s*=\s*(true|false)", ls)
            return m2.group(1) == "true" if m2 else default

        def opt_int(key, default=0):
            m2 = re.search(rf"{key}\s*=\s*(\d+)", ls)
            return int(m2.group(1)) if m2 else default

        def opt_str(key, default=""):
            m2 = re.search(rf'{key}\s*=\s*"([^"]*)"', ls)
            return m2.group(1) if m2 else default

        feeds.append({
            "url":            url,
            "category":       cur_cat,
            "limit":          opt_int("limit", 10),
            "download_full":  opt_bool("download_full_article", False),
            "include_images": opt_bool("include_images", False),
            "enable_filter":  opt_bool("enable_filter", False),
            "filter_element": opt_str("filter_element", ""),
            "block_element":  opt_str("block_element", ""),
            "filtrar_promocoes": opt_bool("filtrar_promocoes", False),
        })

    return feeds


# ── Feed processing ────────────────────────────────────────────────────────

def process_feed(cfg: dict, date_str: str, root_dir: Path,
                 verbose: bool = False,
                 cache: ArticleCache | None = None,
                 feed_ttl: int = 60) -> tuple[bool, str]:
    """
    Process one feed: fetch entries, download articles, write EPUB.
    Returns (ok, log) — log printed atomically by the calling thread.
    """
    url   = cfg["url"]
    limit = cfg["limit"] or 999
    full  = cfg["download_full"]
    log: list[str] = []
    L = log.append

    cat_name = short_feed_name(cfg["category"])
    folder   = root_dir / date_str / cat_name

    L(f"\n  [{cfg['category']}]")
    try:
        feed = None
        if cache and feed_ttl > 0:
            feed = cache.get_feed(url, max_min=feed_ttl)
            if feed is not None and not feed.entries:
                feed = None
        if feed is None:
            feed = fetch_feed(url)
            if cache and feed.entries:
                cache.save_feed(url, feed)
        else:
            L("  (feed cached)")
    except Exception as e:
        L(f"  ✗ {url}\n    Error: {e}")
        return False, "\n".join(log)

    if feed.bozo and not feed.entries:
        L(f"  ✗ {url}\n    Invalid feed or unreachable")
        return False, "\n".join(log)

    name    = feed_display_name(feed, url)
    entries = feed.entries[:limit]
    L(f"  {name}  ({len(entries)} entries)")

    # Pre-filter promotional articles
    entries = [e for e in entries
               if not (cfg.get("filtrar_promocoes")
                       and _PROMO_TITLE.search(
                           clean_text(getattr(e, "title", ""))))]
    total = len(entries)

    def process_entry(idx_entry: tuple) -> tuple:
        i, entry = idx_entry
        title   = clean_text(getattr(entry, "title", "No title"))
        link    = getattr(entry, "link", "")
        dp      = format_pub_date(entry)
        summary = clean_html(getattr(entry, "summary", "")
                             or getattr(entry, "description", ""))
        if len(summary) > 500:
            summary = summary[:497] + "..."

        blocks = None
        status = ""

        if full and link:
            from_cache = bool(cache and cache.get(link))
            with _domain_semaphore(link):
                blocks = download_article(link, cfg, verbose=False,
                                          cache=cache, rss_title=title)
                if not from_cache:
                    time.sleep(0.2)
            if blocks:
                ni     = sum(1 for b in blocks if b["t"] == "img")
                detail = f"  {len(blocks)} blocks" + (f", {ni} img" if ni else "")
                status = f"{'cache' if from_cache else '↓'} ✓{detail}"
            else:
                blocks = blocks_from_entry(entry, cfg.get("include_images", False))
                status = (f"✓ feed  {len(blocks)} blocks"
                          if blocks else "✗ summary")
        else:
            blocks = blocks_from_entry(entry, cfg.get("include_images", False))
            status = f"feed {len(blocks)} blocks" if blocks else "summary"

        return (i, title, link, dp, summary, blocks,
                f"    [{i}/{total}] {title[:50]}  {status}")

    # Parallel article downloads
    results: dict = {}
    with ThreadPoolExecutor(max_workers=ARTICLE_WORKERS) as pool:
        futures = {
            pool.submit(process_entry, (i, e)): i
            for i, e in enumerate(entries, 1)
        }
        for future in as_completed(futures):
            try:
                r = future.result()
                results[r[0]] = r
            except Exception as e:
                i = futures[future]
                results[i] = (i, "?", "", "", "", None,
                              f"    [{i}] ✗ error: {e}")

    articles = []
    for i in sorted(results):
        i_, title, link, dp, summary, blocks, log_line = results[i]
        L(log_line)
        articles.append((title, link, dp, summary, blocks))

    if not articles:
        L("  No articles")
        return False, "\n".join(log)

    filename = f"{short_feed_name(name)}.epub"
    path     = folder / filename
    folder.mkdir(parents=True, exist_ok=True)
    kb = generate_epub(path, name, articles, date_str, cache=cache)
    L(f"  → {date_str}/{cat_name}/{path.name}  ({kb} KB)")
    return True, "\n".join(log)


# ── Folder cleanup ─────────────────────────────────────────────────────────

def clean_old_folders(root_dir: Path, days: int) -> list[str]:
    from datetime import timedelta
    import shutil
    cutoff  = date.today() - timedelta(days=days)
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    removed = []
    for entry in sorted(root_dir.iterdir()):
        if not entry.is_dir() or not pattern.match(entry.name):
            continue
        try:
            d = date.fromisoformat(entry.name)
        except ValueError:
            continue
        if d < cutoff:
            shutil.rmtree(entry)
            removed.append(entry.name)
    return removed


# ── Main ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        prog=Path(sys.argv[0]).name,
        description="Download RSS feeds and generate EPUBs for xteink e-reader.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s                               # all feeds\n"
            "  %(prog)s -c ~/koreader/feed_config.lua\n"
            "  %(prog)s --only RTP                   # feeds matching 'RTP'\n"
            "  %(prog)s --only Publico --only Apple\n"
            "  %(prog)s --list                        # show configured feeds\n"
            "  %(prog)s -o ~/Desktop/news             # custom output folder\n"
            "  %(prog)s --clean                       # remove old folders\n"
            "  %(prog)s --no-cache                    # force re-download all\n"
        ),
    )
    ap.add_argument("-c", "--config",
        metavar="FILE", type=Path, default=DEFAULT_CONFIG,
        help=f"KOReader LUA config (default: {DEFAULT_CONFIG})")
    ap.add_argument("-o", "--output",
        metavar="DIR", type=Path, default=OUTPUT_DIR,
        help=f"Output folder (default: {OUTPUT_DIR}/)")
    ap.add_argument("--only",
        metavar="TEXT", action="append", default=[],
        help="Filter by feed URL or category name (repeatable).")
    ap.add_argument("--list",
        action="store_true",
        help="List configured feeds and exit.")
    ap.add_argument("-v", "--verbose",
        action="store_true",
        help="Per-article diagnostics. Forces single-process mode.")
    ap.add_argument("-w", "--workers",
        metavar="N", type=int, default=0,
        help="Parallel feed workers (default: 0 = one per feed).")
    ap.add_argument("--cache",
        metavar="FILE", type=Path, default=None,
        help="SQLite cache file (default: <output>/cache.db).")
    ap.add_argument("--no-cache",
        action="store_true",
        help="Disable cache — re-download everything.")
    ap.add_argument("--feed-ttl",
        metavar="MIN", type=int, default=60,
        help="Minutes to cache feed responses (default: 60). 0 = no feed cache.")
    ap.add_argument("-d", "--days",
        metavar="N", type=int, default=7,
        help="Days of news to keep (default: 7).")
    ap.add_argument("--clean",
        action="store_true",
        help="Remove old folders without downloading (uses --days).")

    args = ap.parse_args()
    feeds = parse_config(args.config)

    if args.list:
        print(f"\n  Feeds in {args.config}:\n")
        prev_cat = None
        for f in feeds:
            if f["category"] != prev_cat:
                prev_cat = f["category"]
                print(f"\n  [{prev_cat}]")
            mode = "full" if f["download_full"] else "summary"
            lim  = f"max {f['limit']}" if f["limit"] else "no limit"
            print(f"    {f['url']}")
            print(f"      {mode} · {lim}")
        print()
        sys.exit(0)

    args.output.mkdir(parents=True, exist_ok=True)
    date_str = date.today().strftime("%Y-%m-%d")

    cache: ArticleCache | None = None
    if not args.no_cache:
        cache_path = args.cache or (args.output / "cache.db")
        cache = ArticleCache(cache_path)

    if args.clean:
        print(f"\n  Cleaning folders older than {args.days} days...")
        removed = clean_old_folders(args.output, args.days)
        if removed:
            for r in removed:
                print(f"  ✗ removed: {r}/")
            print(f"  {len(removed)} folder(s) removed.")
        else:
            print("  Nothing to remove.")
        print()
        sys.exit(0)

    active = [f for f in feeds
              if not args.only or any(
                  s.lower() in (f["url"] + " " + f["category"]).lower()
                  for s in args.only)]

    n_workers = max(1, args.workers if args.workers > 0 else len(active))

    print(f"\n{'='*60}")
    print(f"  xteink news — {date_str}")
    print(f"  Config  : {args.config}")
    print(f"  Output  : {args.output}/{date_str}/")
    print(f"  Feeds   : {len(active)}")
    print(f"  Workers : {n_workers}")
    print(f"  Keep    : {args.days} days")
    print(f"  Cache   : {cache_path if cache else 'disabled'}")
    if args.only:
        print(f"  Filter  : {', '.join(args.only)}")
    print(f"{'='*60}")

    t_start   = time.time()
    generated = 0
    errors    = 0

    if n_workers == 1 or args.verbose:
        for cfg in active:
            ok, log = process_feed(cfg, date_str, args.output,
                                   verbose=args.verbose,
                                   cache=cache, feed_ttl=args.feed_ttl)
            print(log)
            generated += ok
            errors    += not ok
    else:
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = {
                pool.submit(process_feed, cfg, date_str, args.output,
                            False, cache, args.feed_ttl): cfg
                for cfg in active
            }
            for future in as_completed(futures):
                cfg = futures[future]
                try:
                    ok, log = future.result()
                    print(log)
                except Exception as e:
                    print(f"\n  ✗ Error in {cfg['url']}: {e}")
                    ok = False
                generated += ok
                errors    += not ok

    m = int((time.time() - t_start) // 60)
    s = int((time.time() - t_start) % 60)

    removed = []
    n_cache = 0
    if generated > 0:
        removed = clean_old_folders(args.output, args.days)
        if cache:
            n_cache = cache.clean(args.days * 3)

    print(f"\n{'='*60}")
    print(f"  Done in {m}m {s}s")
    print(f"  EPUBs generated : {generated}")
    print(f"  Errors/skipped  : {errors}")
    print(f"  Output          : {args.output.resolve()}/{date_str}/")
    if removed:
        print(f"  Removed         : {', '.join(removed)}")
    if n_cache:
        print(f"  Cache cleaned   : {n_cache} old entries")
    print(f"{'='*60}\n")
