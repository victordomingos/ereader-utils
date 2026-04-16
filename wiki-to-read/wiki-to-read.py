#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wiki-to-read.py
Generates Wikipedia EPUBs from the official XML dump.

Requires Python 3.10+ (uses pathlib and match/case).

Folder structure:
  wikipedia/pt/
    A/ AB/ WIKI_ABA.epub  ← NCX chapters: Abade, Abadia, Abalado...
    F/ FE/ WIKI_FER.epub  ← NCX chapters: Fernando Pessoa, Fernão de Magalhães...

LANGUAGES: pt  en  es  (same dump format for all)

Parameters:
  -l / --lang              language (default: pt)
  -m / --min-chars         minimum wikitext chars (default: 2000)
  -D / --featured-only     featured articles only ★
  -B / --include-good      with -D: also include good articles
  -w / --workers           parallel workers (default: 1, 0=auto)
  -d / --dump              path to the bz2 dump file

Dependencies: requests  (pip install requests)

Usage:
  python3 wiki-to-read.py -l pt --download-dump
  python3 wiki-to-read.py -l pt
  python3 wiki-to-read.py -l pt -m 3000
  python3 wiki-to-read.py -l pt -D          # featured only ★
  python3 wiki-to-read.py -l pt -D -B       # featured + good
  python3 wiki-to-read.py -l en -D
  python3 wiki-to-read.py -l pt A B
  python3 wiki-to-read.py -i
  python3 wiki-to-read.py -l pt -v
  python3 wiki-to-read.py -l pt --info
"""

import re, sys, bz2, time, zipfile, sqlite3, unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime
from collections import defaultdict
from pathlib import Path

# ── Install requests ──────────────────────────────────────────────────────
def install_deps():
    import subprocess
    try:
        import requests
    except ImportError:
        print("  Installing requests...")
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "requests", "-q"])

install_deps()
import requests

# ── Per-language configuration ────────────────────────────────────────────

LANGUAGES = {
    "pt": {
        "name":    "Português",
        "wiki":    "ptwiki",
        "letters": list("ABCDEFGHIJLMNOPQRSTUVXZ"),
        "sections_remove": {
            "referências", "referencia", "referências e notas",
            "notas", "notas e referências",
            "ver também", "veja também",
            "ligações externas", "links externos",
            "bibliografia", "leitura adicional",
            "galeria", "galeria de imagens",
            "fontes", "fontes e referências",
            "notas de rodapé",
        },
        "tmpl_featured": {"artigo destacado", "featured article"},
        "tmpl_good":     {"artigo bom", "good article"},
    },
    "en": {
        "name":    "English",
        "wiki":    "enwiki",
        "letters": list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
        "sections_remove": {
            "references", "notes", "footnotes",
            "notes and references", "citations",
            "see also", "further reading",
            "external links", "bibliography", "sources", "gallery",
        },
        "tmpl_featured": {"featured article"},
        "tmpl_good":     {"good article"},
    },
    "es": {
        "name":    "Español",
        "wiki":    "eswiki",
        "letters": list("ABCDEFGHIJLMNOPQRSTUVXYZ"),
        "sections_remove": {
            "referencias", "notas", "notas y referencias",
            "bibliografía", "fuentes",
            "véase también", "see also",
            "enlaces externos", "links externos",
            "galería", "galería de imágenes",
            "lecturas adicionales",
        },
        "tmpl_featured": {"artículo destacado", "featured article"},
        "tmpl_good":     {"artículo bueno", "good article"},
    },
}

# ── Global parameters ─────────────────────────────────────────────────────
DUMP_DIR:      Path = Path("dumps")
OUTPUT_FOLDER: Path = Path()
CACHE_DB:      Path = Path()
UA:            dict = {}
CFG:           dict = {}

def configure_language(code: str) -> None:
    global OUTPUT_FOLDER, CACHE_DB, UA, CFG
    if code not in LANGUAGES:
        print(f"\n  ✗ Language '{code}' is not supported.")
        print(f"  Available: {', '.join(LANGUAGES)}")
        sys.exit(1)
    CFG           = {**LANGUAGES[code], "code": code}
    OUTPUT_FOLDER = Path("wikipedia") / code
    CACHE_DB      = OUTPUT_FOLDER / "_cache.db"
    OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)
    DUMP_DIR.mkdir(exist_ok=True)
    UA["User-Agent"] = (
        f"WikipediaEPUB/1.0 "
        f"(epub e-reader offline; language={code}; personal use; python-requests)"
    )

def dump_url(code: str) -> str:
    wiki = LANGUAGES[code]["wiki"]
    return (f"https://dumps.wikimedia.org/{wiki}/latest/"
            f"{wiki}-latest-pages-articles.xml.bz2")

def dump_path_default(code: str) -> Path:
    wiki = LANGUAGES[code]["wiki"]
    return DUMP_DIR / f"{wiki}-latest-pages-articles.xml.bz2"

# ── Utilities ─────────────────────────────────────────────────────────────

def ascii_norm(s: str) -> str:
    return unicodedata.normalize("NFD", s).encode("ascii","ignore").decode().upper()

def _tag_local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag

# ── SQLite progress cache ─────────────────────────────────────────────────

_db_conn: sqlite3.Connection | None = None

def _db() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is None:
        _db_conn = sqlite3.connect(CACHE_DB)
        _db_conn.execute(
            "CREATE TABLE IF NOT EXISTS progress "
            "(pref3 TEXT PRIMARY KEY, n_articles INT, kb INT, ts TEXT)"
        )
        _db_conn.execute("PRAGMA journal_mode=WAL")
        _db_conn.commit()
    return _db_conn

def progress_get(pref3: str) -> int | None:
    r = _db().execute(
        "SELECT n_articles FROM progress WHERE pref3=?", (pref3,)
    ).fetchone()
    return r[0] if r else None

def progress_set(pref3: str, n_articles: int, kb: int) -> None:
    _db().execute(
        "INSERT OR REPLACE INTO progress(pref3,n_articles,kb,ts) VALUES(?,?,?,?)",
        (pref3, n_articles, kb, datetime.now().isoformat())
    )
    _db().commit()

def cache_close() -> None:
    global _db_conn
    if _db_conn:
        _db_conn.commit()
        _db_conn.close()
        _db_conn = None

# ── Dump download ─────────────────────────────────────────────────────────

def download_dump(code: str, destination: Path | None = None) -> Path:
    url  = dump_url(code)
    dest = destination or dump_path_default(code)
    name = LANGUAGES[code]["name"]

    if dest.exists():
        size_mb = dest.stat().st_size // (1024 * 1024)
        print(f"  Dump already exists: {dest}  ({size_mb} MB)")
        if input("  Download again? [y/N] ").strip().lower() != "y":
            return dest

    print(f"\n  Downloading {name} Wikipedia dump...")
    print(f"  URL  : {url}")
    print(f"  Dest : {dest}\n")

    try:
        r = requests.get(url, headers=UA, stream=True, timeout=60)
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done  = 0
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    mb  = done  // (1024 * 1024)
                    tmb = total // (1024 * 1024)
                    bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                    print(f"\r  [{bar}] {pct}%  {mb}/{tmb} MB   ",
                          end="", flush=True)
        print(f"\n  ✓ Download complete: {dest}")
        return dest
    except Exception as e:
        print(f"\n  ✗ Error: {e}")
        dest.unlink(missing_ok=True)
        sys.exit(1)

# ── Featured / good article detection ────────────────────────────────────

_RE_TMPL = re.compile(r"\{\{\s*([^|}{\n]+?)\s*(?:\|[^}]*)?\}\}", re.IGNORECASE)

def article_quality(wikitext: str) -> str | None:
    tmpl_featured = CFG.get("tmpl_featured", set())
    tmpl_good     = CFG.get("tmpl_good", set())
    for m in _RE_TMPL.finditer(wikitext):
        name = m.group(1).strip().lower()
        if name in tmpl_featured:
            return "featured"
        if name in tmpl_good:
            return "good"
    return None

# ── Wikitext parser ───────────────────────────────────────────────────────

def wikitext_to_blocks(
    title: str,
    text: str,
    sections_remove: set,
    min_chars: int,
) -> list | None:
    if text.lstrip().lower().startswith(("#redirect", "#redirec")):
        return None
    if len(text) < min_chars:
        return None

    blocks          = []
    in_skip_section = False
    skip_level      = 0

    for line in text.splitlines():
        # Section heading
        if m := re.match(r"^(={2,4})\s*(.+?)\s*\1\s*$", line):
            level         = len(m.group(1))
            section_title = clean_inline(m.group(2)).strip().lower()
            if section_title in sections_remove:
                in_skip_section = True
                skip_level      = level
            else:
                if in_skip_section and level <= skip_level:
                    in_skip_section = False
                if not in_skip_section:
                    tag = "h2" if level == 2 else "h3"
                    if t := clean_inline(m.group(2)).strip():
                        blocks.append({"t": tag, "x": t})
            continue

        if in_skip_section:
            continue

        line = line.strip()
        if not line:
            continue

        # Skip block templates, tables, categories, files
        if (line.startswith(("{{", "|}", "{|", "|", "!"))
                or re.match(r"\[\[(Ficheiro|File|Image|Imagem|Categoria|"
                            r"Category|Archivo|Categoría):", line, re.I)):
            continue

        # List item (level 1 only)
        if line[0] in ("*", "#") and line[1:2] not in ("*", "#", ":"):
            if t := clean_inline(line[1:].strip()):
                if len(t) > 10:
                    blocks.append({"t": "li", "x": t})
            continue

        # Regular paragraph
        if t := clean_inline(line):
            if len(t) > 30:
                blocks.append({"t": "p", "x": t})

    if sum(1 for b in blocks if b["t"] == "p") < 2:
        return None

    return blocks


def clean_inline(t: str) -> str:
    t = re.sub(r"\[\[(?:Ficheiro|File|Image|Imagem|Archivo)[^\]]*\]\]",
               "", t, flags=re.I)
    t = re.sub(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]", r"\1", t)
    t = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", t)
    t = re.sub(r"\[https?://\S+\]", "", t)
    t = re.sub(r"\{\{(?:lang|língua|lien|Lien)[^|]*\|([^|}]+)[^}]*\}\}",
               r"\1", t, flags=re.I)
    for _ in range(5):
        if (t_new := re.sub(r"\{\{[^{}]*\}\}", "", t)) == t:
            break
        t = t_new
    t = re.sub(r"'{2,3}", "", t)
    t = re.sub(r"<ref[^>]*>.*?</ref>", "", t, flags=re.DOTALL)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\s+", " ", t).strip()

# ── Top-level function for multiprocessing (must be picklable) ────────────

def parse_batch(
    items: list[tuple],
    sections_remove: set,
    min_chars_map: dict,
) -> tuple[list, dict]:
    """
    Processes a batch of articles in parallel.
    Called in a separate worker — does not access global state.

    items           : [(title, wikitext, tn, min_chars_eff), ...]
    sections_remove : set of section names to skip
    min_chars_map   : {title: min_chars_eff} — not used directly,
                      already included in each item

    Returns (results, partial_stats).
    results = [(pref3, title, blocks), ...]
    """
    results = []
    stats   = {"accepted": 0, "short": 0}

    for title, wikitext, tn, min_chars_eff in items:
        blocks = wikitext_to_blocks(title, wikitext,
                                    sections_remove, min_chars_eff)
        if blocks:
            pref3 = tn[:3] if len(tn) >= 3 else tn.ljust(3, "_")
            results.append((pref3, title, blocks))
            stats["accepted"] += 1
        else:
            stats["short"] += 1

    return results, stats

# ── XML dump parser ───────────────────────────────────────────────────────

# Batch size sent to each worker.
# Larger values → less IPC overhead, more memory per batch.
BATCH_SIZE = 500

def parse_dump(
    filename: Path,
    min_chars: int,
    letters_f: set | None        = None,
    prefixes2_f: set | None      = None,
    prefixes3_f: set | None      = None,
    featured_only: bool          = False,
    include_good: bool           = False,
    n_workers: int               = 1,
) -> tuple[dict, dict]:
    """
    Reads the XML dump (bz2) and groups parsed articles by 3-letter prefix.

    n_workers=1 : single-process (no IPC overhead)
    n_workers>1 : ProcessPoolExecutor with n_workers workers
                  The XML loop runs in the main process;
                  wikitext parsing runs in parallel.
    """
    sections_remove = CFG["sections_remove"]
    stats = {
        "read": 0, "accepted": 0, "short": 0,
        "redirects": 0, "filtered": 0, "not_featured": 0,
    }
    groups     = defaultdict(list)
    total_size = filename.stat().st_size
    last_pct   = -1
    t_start    = time.time()

    print(f"  Processing: {filename.name}")
    if n_workers > 1:
        print(f"  Workers    : {n_workers}  (batch={BATCH_SIZE} articles)")
    if featured_only:
        mode = "featured + good" if include_good else "featured only ★"
        print(f"  Filter     : {mode}")
    else:
        print(f"  min-chars  : {min_chars:,}")

    fh = bz2.open(filename, "rb") if filename.suffix == ".bz2" \
         else filename.open("rb")

    # Pending batch to send to workers
    pending_batch: list[tuple] = []

    def _flush_batch(executor, futures, batch):
        """Sends batch to pool or processes directly if single-worker."""
        if not batch:
            return
        if executor:
            f = executor.submit(parse_batch, batch, sections_remove, {})
            futures.append(f)
        else:
            results, st = parse_batch(batch, sections_remove, {})
            _aggregate(results, st)

    def _aggregate(results, st):
        for pref3, title, blocks in results:
            if not prefixes3_f or pref3 in prefixes3_f:
                groups[pref3].append((title, blocks))
        stats["accepted"] += st["accepted"]
        stats["short"]    += st["short"]

    executor = ProcessPoolExecutor(max_workers=n_workers) \
               if n_workers > 1 else None
    futures: list = []

    try:
        current_title = None
        current_ns    = None

        for _, elem in ET.iterparse(fh, events=("end",)):
            match _tag_local(elem.tag):

                case "title":
                    current_title = elem.text or ""
                    elem.clear()

                case "ns":
                    current_ns = elem.text
                    elem.clear()

                case "text":
                    if current_ns != "0" or not current_title:
                        elem.clear()
                        continue

                    title = current_title
                    tn    = ascii_norm(title)

                    if ":" in title or "/" in title:
                        elem.clear()
                        continue

                    if letters_f and (not tn or tn[0] not in letters_f):
                        stats["filtered"] += 1
                        elem.clear()
                        continue
                    if prefixes2_f and tn[:2] not in prefixes2_f:
                        stats["filtered"] += 1
                        elem.clear()
                        continue

                    stats["read"] += 1
                    wikitext = elem.text or ""

                    if wikitext.lstrip().lower().startswith(
                            ("#redirect", "#redirec")):
                        stats["redirects"] += 1
                        elem.clear()
                        continue

                    # Quick filters (in main process, before dispatching)
                    if featured_only:
                        quality = article_quality(wikitext)
                        accept  = (quality == "featured"
                                   or (include_good and quality == "good"))
                        if not accept:
                            stats["not_featured"] += 1
                            elem.clear()
                            continue
                        min_chars_eff = 0
                    else:
                        if len(wikitext) < min_chars:
                            stats["short"] += 1
                            elem.clear()
                            continue
                        min_chars_eff = min_chars

                    # Accumulate in batch
                    pending_batch.append((title, wikitext, tn, min_chars_eff))

                    # Flush batch when full
                    if len(pending_batch) >= BATCH_SIZE:
                        _flush_batch(executor, futures, pending_batch.copy())
                        pending_batch.clear()

                        # Collect completed futures to avoid memory build-up
                        if executor and len(futures) > n_workers * 2:
                            done = [f for f in futures if f.done()]
                            for f in done:
                                _aggregate(*f.result())
                                futures.remove(f)

                    elem.clear()

                case "page":
                    try:
                        pos = fh.tell() if hasattr(fh, "tell") else 0
                        pct = min(99, pos * 100 // total_size)
                        if pct != last_pct:
                            elapsed  = time.time() - t_start
                            per_min  = stats["read"] / max(1, elapsed) * 60
                            print(f"\r  {pct}%  "
                                  f"read={stats['read']:,}  "
                                  f"accepted={stats['accepted']:,}  "
                                  f"({per_min:.0f} art/min)   ",
                                  end="", flush=True)
                            last_pct = pct
                    except Exception:
                        pass
                    current_title = None
                    current_ns    = None
                    elem.clear()

        # Flush the final batch
        _flush_batch(executor, futures, pending_batch)
        pending_batch.clear()

        # Wait for all remaining futures
        if executor:
            for f in as_completed(futures):
                _aggregate(*f.result())

    except KeyboardInterrupt:
        print(f"\n\n  Interrupted. Saving progress...")
        if executor:
            executor.shutdown(wait=False, cancel_futures=True)
        cache_close()
        sys.exit(0)
    finally:
        if executor:
            executor.shutdown(wait=True)
        fh.close()

    print(f"\r  100%  "
          f"read={stats['read']:,}  "
          f"accepted={stats['accepted']:,}          ")
    return groups, stats

# ── CSS and HTML ──────────────────────────────────────────────────────────

CSS = """
body  { font-family: sans-serif; margin: 0.8em; line-height: 1.55; }
h1    { font-size: 1.15em; font-weight: bold; margin: 0.4em 0 0.2em 0;
        border-bottom: 1px solid #bbb; padding-bottom: 0.15em; }
h2    { font-size: 1.0em; font-weight: bold;
        margin: 1.0em 0 0.2em 0; color: #222; }
h3    { font-size: 0.9em; font-weight: bold;
        margin: 0.7em 0 0.15em 0; color: #444; }
p     { margin: 0.25em 0 0.45em 0; font-size: 0.88em; }
li    { font-size: 0.85em; margin: 0.1em 0; list-style: disc; }
ul    { margin: 0.2em 0 0.3em 1.2em; padding: 0; }
.meta { font-size: 0.72em; color: #999; margin-bottom: 0.6em; }
.sep  { border: none; border-top: 2px solid #ccc; margin: 1.0em 0; }
hr    { border: none; border-top: 1px solid #eee; margin: 0.4em 0; }
"""

def _esc(t: str) -> str:
    return (t.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))

def article_to_html(title: str, blocks: list) -> str:
    parts = [f"<h1>{_esc(title)}</h1>"]
    for b in blocks:
        x = _esc(b["x"])
        match b["t"]:
            case "h2": parts.append(f"<h2>{x}</h2>")
            case "h3": parts.append(f"<h3>{x}</h3>")
            case "p":  parts.append(f"<p>{x}</p>")
            case "li": parts.append(f"<li>{x}</li>")
    return "\n".join(parts)

def xhtml_page(page_title: str, body: str) -> str:
    code = CFG.get("code", "en")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{code}">
<head><meta charset="utf-8"/>
<title>{_esc(page_title)}</title>
<link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>{body}</body>
</html>"""

# ── EPUB generation ───────────────────────────────────────────────────────

ARTICLES_PER_PAGE = 20

def generate_epub(path: Path, pref3: str, articles: list) -> int:
    code  = CFG["code"]
    name  = CFG["name"]
    title = f"Wiki {name} — {pref3}"
    uid   = f"wiki-{code}-{pref3.lower()}"

    pages:  list[tuple] = []
    manids: list[tuple] = []
    navpts: list[tuple] = []

    # Index page
    index_html = (f"<h1>{_esc(title)}</h1><hr/>"
                  f"<p class='meta'>{len(articles)} articles · "
                  f"Wikipedia {name} · {date.today().isoformat()}</p><hr/>\n"
                  + "".join(f"<p>{_esc(t)}</p>\n" for t, _ in articles))

    pages.append(("i0", "i.html", xhtml_page(title, index_html)))
    manids.append(("i0", "i.html"))
    navpts.append(("n0", "i.html", "Index"))

    page_idx = 0
    nav_idx  = 1

    for chunk_start in range(0, len(articles), ARTICLES_PER_PAGE):
        chunk = articles[chunk_start:chunk_start + ARTICLES_PER_PAGE]
        pid   = f"c{page_idx:03d}"
        fname = f"{pid}.html"
        body  = ""
        anchors = []

        for art_idx, (art_title, blocks) in enumerate(chunk):
            aid   = f"a{chunk_start + art_idx}"
            body += f'<div id="{aid}">\n'
            body += article_to_html(art_title, blocks)
            body += '\n<hr class="sep"/></div>\n'
            anchors.append((aid, art_title))

        pages.append((pid, fname, xhtml_page(f"{pref3} — p.{page_idx + 1}", body)))
        manids.append((pid, fname))
        for aid, art_title in anchors:
            navpts.append((f"n{nav_idx}", f"{fname}#{aid}", art_title))
            nav_idx += 1
        page_idx += 1

    manifest = "\n    ".join(
        f'<item id="{pid}" href="{fn}" media-type="application/xhtml+xml"/>'
        for pid, fn in manids
    )
    manifest += '\n    <item id="css" href="style.css" media-type="text/css"/>'
    manifest += '\n    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'

    spine   = "\n    ".join(f'<itemref idref="{pid}"/>' for pid, _ in manids)
    nav_xml = "\n    ".join(
        f'<navPoint id="{nid}" playOrder="{i}">'
        f'<navLabel><text>{_esc(label)}</text></navLabel>'
        f'<content src="{_esc(src)}"/></navPoint>'
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
    <dc:language>{code}</dc:language>
    <dc:identifier id="uid">{uid}</dc:identifier>
    <dc:source>https://{code}.wikipedia.org</dc:source>
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

    return path.stat().st_size // 1024

# ── Mode: list languages ──────────────────────────────────────────────────

def mode_list_languages() -> None:
    print(f"\n{'='*65}")
    print(f"  Supported languages")
    print(f"{'='*65}")
    for code, cfg in LANGUAGES.items():
        dest   = dump_path_default(code)
        status = "✓ local" if dest.exists() else "— not downloaded"
        print(f"  {code}  {cfg['name']:<12}  {status}")
        print(f"       {dump_url(code)}")
    print(f"{'='*65}\n")

# ── Mode: diagnostics ─────────────────────────────────────────────────────

def mode_diagnostics(filename: Path, min_chars: int, n: int = 5) -> None:
    print(f"\n{'='*65}")
    print(f"  Diagnostics: {filename.name}")
    print(f"  min_chars={min_chars}  language={CFG['code']}")
    print(f"{'='*65}\n")

    sections_remove = CFG["sections_remove"]
    found           = 0

    fh = bz2.open(filename, "rb") if filename.suffix == ".bz2" \
         else filename.open("rb")

    current_title = None
    current_ns    = None

    try:
        for _, elem in ET.iterparse(fh, events=("end",)):
            match _tag_local(elem.tag):
                case "title":
                    current_title = elem.text or ""
                    elem.clear()
                case "ns":
                    current_ns = elem.text
                    elem.clear()
                case "text":
                    if current_ns != "0" or not current_title:
                        elem.clear()
                        continue
                    if ":" in current_title or "/" in current_title:
                        elem.clear()
                        continue
                    wt = elem.text or ""
                    if wt.lstrip().lower().startswith(("#redirect","#redirec")):
                        elem.clear()
                        continue
                    if len(wt) < min_chars:
                        elem.clear()
                        continue
                    blocks = wikitext_to_blocks(
                        current_title, wt, sections_remove, min_chars)
                    n_p = sum(1 for b in blocks if b["t"] == "p") if blocks else 0
                    print(f"  Title   : {current_title}")
                    print(f"  WT chars: {len(wt):,}")
                    print(f"  Blocks  : {len(blocks) if blocks else 0}"
                          f"  ({n_p} paragraphs)")
                    if blocks:
                        print(f"  Start   : {blocks[0]['x'][:100]}...")
                    print(f"  parse → {'✓' if blocks else '✗'}")
                    print()
                    found += 1
                    elem.clear()
                    if found >= n:
                        break
                case "page":
                    current_title = None
                    current_ns    = None
                    elem.clear()
    finally:
        fh.close()

    print(f"{'='*65}\n")

# ── Mode: cache info ──────────────────────────────────────────────────────

def mode_info() -> None:
    print(f"\n{'='*65}")
    print(f"  Progress cache  [{CFG['name']}]")
    print(f"  File: {CACHE_DB}")
    print(f"{'='*65}\n")

    if not CACHE_DB.exists():
        print("  No cache yet.\n")
        return

    rows = _db().execute(
        "SELECT pref3, n_articles, kb, ts FROM progress ORDER BY pref3"
    ).fetchall()

    if not rows:
        print("  Cache is empty.\n")
        cache_close()
        return

    total_art = sum(r[1] for r in rows)
    total_kb  = sum(r[2] for r in rows)
    print(f"  Groups generated : {len(rows)}")
    print(f"  Total articles   : {total_art:,}")
    print(f"  Total EPUBs      : {total_kb // 1024} MB")
    print(f"  Last generated   : {rows[-1][0]}  ({rows[-1][3][:16]})")
    print(f"{'='*65}\n")
    cache_close()

# ── Main ──────────────────────────────────────────────────────────────────

def main(
    dump_file: Path,
    min_chars: int,
    letters_proc: list,
    prefixes2: list,
    prefixes3: list,
    featured_only: bool = False,
    include_good: bool  = False,
    n_workers: int      = 1,
) -> None:
    code = CFG["code"]
    name = CFG["name"]

    letters_f   = set(letters_proc) if letters_proc != CFG["letters"] else None
    prefixes2_f = set(prefixes2) if prefixes2 else None
    prefixes3_f = set(prefixes3) if prefixes3 else None

    print(f"\n{'='*65}")
    print(f"  Wikipedia {name}  [{code}]")
    print(f"  Dump      : {dump_file}")
    print(f"  Cache     : {CACHE_DB}")
    print(f"  Output    : wikipedia/{code}/X/XY/WIKI_XYZ.epub")
    if featured_only:
        mode = "featured + good" if include_good else "featured only ★"
        print(f"  Filter    : {mode}")
    else:
        print(f"  min-chars : {min_chars:,}")
    if letters_f:
        print(f"  Letters   : {' '.join(sorted(letters_f))}")
    if prefixes2_f:
        print(f"  Prefixes  : {' '.join(sorted(prefixes2_f))}")
    if prefixes3_f:
        print(f"  Prefixes  : {' '.join(sorted(prefixes3_f))}")
    if n_workers > 1:
        print(f"  Workers   : {n_workers}")
    print(f"{'='*65}\n")

    t_start = time.time()

    groups, stats = parse_dump(
        dump_file, min_chars,
        letters_f=letters_f,
        prefixes2_f=prefixes2_f,
        prefixes3_f=prefixes3_f,
        featured_only=featured_only,
        include_good=include_good,
        n_workers=n_workers,
    )

    t_parse = time.time() - t_start
    print(f"\n  Parse: {t_parse:.0f}s  |  "
          f"accepted={stats['accepted']:,}  "
          f"short={stats['short']:,}  "
          f"redirects={stats['redirects']:,}\n")

    # ── Generate EPUBs ────────────────────────────────────────────────────
    epubs_generated = 0
    epubs_skipped   = 0
    total_articles  = 0

    for pref3 in sorted(groups):
        articles = sorted(groups[pref3], key=lambda x: ascii_norm(x[0]))
        if not articles:
            continue

        folder = OUTPUT_FOLDER / pref3[0] / pref3[:2]
        folder.mkdir(parents=True, exist_ok=True)
        path   = folder / f"WIKI_{pref3}.epub"

        if path.exists() and progress_get(pref3) is not None:
            epubs_skipped += 1
            continue

        kb = generate_epub(path, pref3, articles)
        progress_set(pref3, len(articles), kb)
        print(f"  [{pref3}] {len(articles)} articles · {kb} KB")
        epubs_generated += 1
        total_articles  += len(articles)

    # ── Summary ───────────────────────────────────────────────────────────
    t_total = time.time() - t_start
    h = int(t_total // 3600)
    m = int((t_total % 3600) // 60)
    s = int(t_total % 60)

    read     = stats["read"]
    accepted = stats["accepted"]
    pct_acc  = f"{accepted * 100 // read}%" if read else "—"

    print(f"\n{'='*65}")
    print(f"  SUMMARY — Wikipedia {name} [{code}]")
    print(f"{'='*65}")
    print(f"  Total time        : {h}h {m}m {s}s")
    print(f"    of which parse  : {t_parse:.0f}s")
    print(f"")
    print(f"  ARTICLES")
    print(f"    Read from dump  : {read:,}")
    if featured_only:
        mode = "featured+good" if include_good else "featured ★"
        print(f"    Accepted ({mode}): {accepted:,}")
        print(f"    Not featured    : {stats['not_featured']:,}")
    else:
        print(f"    Accepted (≥{min_chars:,} chars): {accepted:,}  ({pct_acc})")
        print(f"    Short/stubs     : {stats['short']:,}")
    print(f"    Redirects       : {stats['redirects']:,}")
    print(f"    Filtered        : {stats['filtered']:,}")
    print(f"")
    print(f"  EPUBs")
    print(f"    Generated       : {epubs_generated:,}")
    print(f"    Skipped (exist.): {epubs_skipped:,}")
    print(f"    New articles    : {total_articles:,}")
    print(f"")
    print(f"  Folder            : {OUTPUT_FOLDER.resolve()}/")
    print(f"{'='*65}\n")

    cache_close()

# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        prog=Path(sys.argv[0]).name,
        description="Generates Wikipedia EPUBs from the official XML dump. (Python 3.10+)",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s -l pt --download-dump\n"
            "  %(prog)s -l pt                    # all articles (≥2000 chars)\n"
            "  %(prog)s -l pt -m 3000            # longer articles only\n"
            "  %(prog)s -l pt -D                 # featured only ★\n"
            "  %(prog)s -l pt -D -B              # featured + good\n"
            "  %(prog)s -l en -D                 # English featured articles\n"
            "  %(prog)s -l pt -w 3               # 3 parallel workers\n"
            "  %(prog)s -l pt -w 0               # auto worker count\n"
            "  %(prog)s -l pt A B                # letters A and B\n"
            "  %(prog)s -l pt FER                # prefix FER\n"
            "  %(prog)s -i                       # list languages\n"
            "  %(prog)s -l pt -v                 # diagnostics\n"
            "  %(prog)s -l pt --info             # cache statistics\n"
        ),
    )
    ap.add_argument("-l","--lang",
        metavar="CODE", default="pt",
        help="Language code (default: pt). Available: pt en es")
    ap.add_argument("-m","--min-chars",
        metavar="N", type=int, default=2000,
        help="Minimum wikitext characters (default: 2000).\n"
             "  500  → ~750k articles  ~14 GB\n"
             "  2000 → ~400k articles  ~7-8 GB  [recommended]\n"
             "  5000 → ~170k articles  ~3 GB\n"
             "  Ignored when using -D.")
    ap.add_argument("-D","--featured-only",
        action="store_true",
        help="Featured articles only ★ (~1,500 PT / ~6,500 EN).\n"
             "Ignores --min-chars. Size: ~80 MB PT / ~500 MB EN.")
    ap.add_argument("-B","--include-good",
        action="store_true",
        help="With -D: also include good articles (~3,200 PT).\n"
             "Total size with -D -B: ~200 MB PT.")
    ap.add_argument("-w","--workers",
        metavar="N", type=int, default=1,
        help="Number of parallel processes for wikitext parsing.\n"
             "  1 (default) : single-process, easier to debug\n"
             "  2-4         : recommended for machines with 4+ cores\n"
             "  0           : uses cpu_count()-1 automatically")
    ap.add_argument("-d","--dump",
        metavar="FILE", type=Path,
        help="Path to the bz2 dump file (default: dumps/ folder).")
    ap.add_argument("--download-dump",
        action="store_true",
        help="Download the language dump and exit.")
    ap.add_argument("-i","--list-languages",
        action="store_true",
        help="List supported languages and exit.")
    ap.add_argument("-v","--verbose",
        action="store_true",
        help="Diagnostics: show the first parsed articles.")
    ap.add_argument("--info",
        action="store_true",
        help="Show progress cache statistics.")
    ap.add_argument("prefixes",
        nargs="*", metavar="LETTER_OR_PREFIX",
        help="Letters (A), 2-letter prefixes (AB) or 3-letter prefixes (FER).")

    args = ap.parse_args()

    if args.list_languages:
        mode_list_languages()
        sys.exit(0)

    configure_language(args.lang.lower())

    if args.download_dump:
        download_dump(args.lang.lower(), args.dump)
        sys.exit(0)

    if args.info:
        mode_info()
        sys.exit(0)

    dump_file = args.dump or dump_path_default(args.lang.lower())
    if not dump_file.exists():
        print(f"\n  ✗ Dump not found: {dump_file}")
        print(f"  Download it with:")
        print(f"     python3 {Path(sys.argv[0]).name} "
              f"-l {args.lang} --download-dump\n")
        sys.exit(1)

    if args.verbose:
        mode_diagnostics(dump_file, args.min_chars)
        sys.exit(0)

    tokens       = [a.upper() for a in args.prefixes
                    if a.replace("-", "").isalpha()]
    letters_proc = sorted({a for a in tokens if len(a) == 1}) or CFG["letters"]
    prefixes2    = sorted({a for a in tokens if len(a) == 2})
    prefixes3    = sorted({a for a in tokens if len(a) == 3})

    # Resolve n_workers=0 → automatic
    import os as _os
    n_workers = args.workers
    if n_workers == 0:
        n_workers = max(1, (_os.cpu_count() or 2) - 1)
        print(f"  Auto workers: {n_workers} (cpu_count-1)")

    main(dump_file, args.min_chars, letters_proc, prefixes2, prefixes3,
         featured_only=args.featured_only,
         include_good=args.include_good,
         n_workers=n_workers)
