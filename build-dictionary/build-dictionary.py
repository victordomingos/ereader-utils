#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build-dictionary.py
Builds dictionary EPUBs from Wiktionary dumps.

Folder structure:
  dictionaries/
    pt/
      A/ AB/ DICT_ABA.epub ...
    es/
      A/ AB/ DICT_ABA.epub ...

LANGUAGES: pt  es  en  fr  de  it  ca  gl

Dependencies: requests (only for download and --update-since)
  pip install requests

Usage:
  python3 build-dictionary.py -l pt --download-dump
  python3 build-dictionary.py -l pt
  python3 build-dictionary.py -l pt A B
  python3 build-dictionary.py -l pt AB ABA
  python3 build-dictionary.py -i
  python3 build-dictionary.py -l pt -c
  python3 build-dictionary.py -l pt -a 2025-06-01
  python3 build-dictionary.py -l pt -v   # dump diagnostics
"""

import os, re, sys, json, time, zipfile, unicodedata, bz2, sqlite3
import xml.etree.ElementTree as ET
from datetime import date
from collections import defaultdict

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
#
# section_re: regex to find the language section heading in wikitext.
# Each Wiktionary may use different formats:
#   - Plain text:  ==Portuguese==
#   - Template:    == {{língua|pt}} ==
#   - English:     ==English==  (no template)
#
LANGUAGES = {
    "pt": {
        "name":       "Português",
        "wiki":       "pt",
        # PT Wiktionary uses ={{-pt-}}= (level 1, template)
        # Other formats found: =={{-pt-}}= or ==Português==
        "section_re":  r"=+\s*(?:\{\{-pt-\}\}|Portugu[eê]s|\{\{l[ií]ngua\|pt[^}]*\}\})\s*=+",
        "letters":     list("ABCDEFGHIJLMNOPQRSTUVXZ"),
        "seconds":     list("aáàãâbcçdeéêfghiíjklmnoóõôpqrstuúvxyz"),
        # Classes: ==Substantivo== or =={{Substantivo|pt}}==
        "classes":    ["Substantivo","Verbo","Adjetivo","Advérbio","Pronome",
                       "Preposição","Conjunção","Interjeição","Artigo",
                       "Numeral","Locução","Contração"],
        "gender_re":  r"\{\{gramática\|([mfc])[^}]*\}\}|\{\{(mf?|f)\}\}",
    },
    "es": {
        "name":       "Español",
        "wiki":       "es",
        # ES Wiktionary uses == {{lengua|es}} ==
        "section_re":  r"==\s*\{\{[Ll]engua\|es[^}]*\}\}\s*==",
        "letters":     list("ABCDEFGHIJLMNOPQRSTUVXYZ"),
        "seconds":     list("abcdefghijlmnopqrstuvxyzáéíóúüñ"),
        "classes":    ["Sustantivo","Verbo","Adjetivo","Adverbio","Pronombre",
                       "Preposición","Conjunción","Interjección","Artículo",
                       "Numeral","Locución"],
        # ES uses {{sustantivo|es|masculino}} or {{adjetivo|es}} as heading
        "class_re":   r"\{\{(sustantivo|verbo|adjetivo|adverbio|pronombre|"
                      r"preposici[oó]n|conjunci[oó]n|interjecci[oó]n|"
                      r"art[ií]culo|numeral|locuci[oó]n)[^}]*\}\}",
        "gender_re":  r"\{\{(?:sustantivo[^}]*\|)(masculino|femenino|"
                      r"masculino y femenino)[^}]*\}\}|\|mf?\b",
        # ES uses ;1: definition instead of # definition
        "def_re":     r"^;[\d]+[^:]*:\s*",
    },
    "en": {
        "name":       "English",
        "wiki":       "en",
        "section_re":  r"==\s*English\s*==",
        "letters":     list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
        "seconds":     list("abcdefghijklmnopqrstuvwxyz"),
        "classes":    ["Noun","Verb","Adjective","Adverb","Pronoun",
                       "Preposition","Conjunction","Interjection","Article",
                       "Numeral","Phrase","Contraction"],
        "gender_re":  None,
    },
    "fr": {
        "name":       "Français",
        "wiki":       "fr",
        "section_re":  r"==\s*(?:Fran[cç]ais|\{\{langue\|fr[^}]*\}\})\s*==",
        "letters":     list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
        "seconds":     list("abcdefghijklmnopqrstuvwxyzàâæçéèêëîïôœùûüÿ"),
        "classes":    ["Nom","Verbe","Adjectif","Adverbe","Pronom",
                       "Préposition","Conjonction","Interjection","Article",
                       "Numéral","Locution"],
        "gender_re":  r"\{\{(m|f|mf|n)\}\}",
    },
    "de": {
        "name":       "Deutsch",
        "wiki":       "de",
        "section_re":  r"==\s*(?:Deutsch|\{\{Sprache\|Deutsch[^}]*\}\})\s*==",
        "letters":     list("ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÜ"),
        "seconds":     list("abcdefghijklmnopqrstuvwxyzäöüß"),
        "classes":    ["Substantiv","Verb","Adjektiv","Adverb","Pronomen",
                       "Präposition","Konjunktion","Interjektion","Artikel",
                       "Numerale"],
        "gender_re":  r"\{\{([mfn])\}\}",
    },
    "it": {
        "name":       "Italiano",
        "wiki":       "it",
        "section_re":  r"==\s*(?:Italiano|\{\{-it-[^}]*\}\}|\{\{lingua\|it[^}]*\}\})\s*==",
        "letters":     list("ABCDEFGHILMNOPQRSTUVZ"),
        "seconds":     list("abcdefghilmnopqrstuvzàèéìíîòóùú"),
        "classes":    ["Sostantivo","Verbo","Aggettivo","Avverbio","Pronome",
                       "Preposizione","Congiunzione","Interiezione","Articolo",
                       "Numerale","Locuzione"],
        "gender_re":  r"\{\{(m|f|mf|n)\}\}",
    },
    "ca": {
        "name":       "Català",
        "wiki":       "ca",
        # CA Wiktionary uses ==Català== or == {{-ca-}} ==
        "section_re":  r"==\s*(?:Catal[àa]|\{\{-ca-\}\}|\{\{llengua\|ca[^}]*\}\})\s*==",
        "letters":     list("ABCDEFGHIJLMNOPQRSTUVXZ"),
        "seconds":     list("abcdefghijlmnopqrstuvxzàáèéíïòóúüç"),
        "classes":    ["Nom","Verb","Adjectiu","Adverbi","Pronom",
                       "Preposició","Conjunció","Interjecció","Article",
                       "Numeral","Locució","Contracció"],
        "gender_re":  r"\{\{(m|f|mf|n)\}\}",
    },
    "gl": {
        "name":       "Galego",
        "wiki":       "gl",
        # GL uses {{-glref-}} (GL-only article) or {{-gl-}} (multi-language)
        "section_re":  r"\{\{-gl(?:ref)?-\}\}",
        # End of GL section: next language section (2-3 letters, not gl/glref)
        "section_end_re": r"\n\{\{-(?!gl(?:ref)?-)([a-z]{2,3})-\}\}",
        "letters":     list("ABCDEFGHIJLMNOPQRSTUVXZ"),
        "seconds":     list("abcdefghijlmnopqrstuvxzáéíóúàèñ"),
        "classes":    ["Substantivo","Verbo","Adxectivo","Adverbio","Pronome",
                       "Preposición","Conxunción","Interxección","Artigo",
                       "Numeral","Locución"],
        # GL uses {{-substf-|gl}}, {{-substm-|gl}}, {{-verb-|gl}}, etc.
        "class_re":   r"\{\{-(subst[mfc]?|verb[o]?|adx|adv|pron|prep|conx|"
                      r"interx|art|num|loc)[^}]*\}\}",
        "gender_re":  r"\{\{-subst([mfc])-",
        # GL uses {{Gl|X}} and {{gl|X}} as wikilinks (should → X)
        "clean_extra_re": [
            (r"\{\{[Gg]l\|([^|{}]+)\}\}", r"\1"),
            (r"\{\{PAGENAME\}\}", r""),
        ],
    },
}

# ── Global parameters ─────────────────────────────────────────────────────
PAUSE       = 0.5
MAXLAG      = 5
MAX_RETRIES = 5

OUTPUT_FOLDER = ""
API_URL       = ""
CACHE_DB      = ""   # single SQLite file (instead of thousands of JSONs)
DUMP_DIR      = "dumps"
UA            = {}
CFG           = {}

def configure_language(code):
    global OUTPUT_FOLDER, API_URL, CACHE_DB, UA, CFG
    if code not in LANGUAGES:
        print(f"\n  ✗ Language '{code}' is not supported.")
        print(f"  Available: {', '.join(LANGUAGES)}")
        sys.exit(1)
    CFG           = {**LANGUAGES[code], "code": code}
    OUTPUT_FOLDER = os.path.join("dictionaries", code)
    API_URL       = f"https://{CFG['wiki']}.wiktionary.org/w/api.php"
    CACHE_DB      = os.path.join(OUTPUT_FOLDER, "_cache.db")
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    os.makedirs(DUMP_DIR,      exist_ok=True)
    UA["User-Agent"] = (
        f"DictionaryWikimedia/2.0 "
        f"(epub e-reader offline; language={code}; "
        f"personal use; python-requests)"
    )

def dump_url(code):
    w = LANGUAGES[code]["wiki"]
    return (f"https://dumps.wikimedia.org/{w}wiktionary/latest/"
            f"{w}wiktionary-latest-pages-articles.xml.bz2")

def dump_path(code):
    w = LANGUAGES[code]["wiki"]
    return os.path.join(DUMP_DIR, f"{w}wiktionary-latest-pages-articles.xml.bz2")

# ── SQLite cache ──────────────────────────────────────────────────────────
# A single .db file instead of thousands of individual JSONs.
# Table: cache(key TEXT PRIMARY KEY, content TEXT)
#   content = NULL        → network failure (retry)
#   content = ""          → no definition for this language (definitive)
#   content = JSON {...}  → entry with definition

_db_conn = None

def _db():
    global _db_conn
    if _db_conn is None:
        _db_conn = sqlite3.connect(CACHE_DB)
        _db_conn.execute(
            "CREATE TABLE IF NOT EXISTS cache "
            "(key TEXT PRIMARY KEY, content TEXT)"
        )
        _db_conn.execute("PRAGMA journal_mode=WAL")
        _db_conn.execute("PRAGMA synchronous=NORMAL")
        _db_conn.commit()
    return _db_conn

def cache_get(key):
    """
    Returns:
      None        → not in cache (or previous failure → retry)
      {}          → no definition for this language (do not retry)
      dict(entry) → full entry
    """
    try:
        row = _db().execute(
            "SELECT content FROM cache WHERE key=?", (key,)
        ).fetchone()
        if row is None:
            return None          # does not exist
        if row[0] is None:
            return None          # previous network failure → retry
        if row[0] == "":
            return {}            # no definition
        return json.loads(row[0])
    except Exception:
        return None

def cache_set(key, entry):
    """
    entry = None → network failure (stores NULL → will be retried)
    entry = {}   → no definition (stores "" → not retried)
    entry = dict → full entry (stores JSON)
    """
    try:
        if entry is None:
            content = None
        elif entry == {}:
            content = ""
        else:
            content = json.dumps(entry, ensure_ascii=False)
        _db().execute(
            "INSERT OR REPLACE INTO cache(key, content) VALUES(?,?)",
            (key, content)
        )
        # Periodic commit (not per word — much faster)
    except Exception:
        pass

def cache_commit():
    try:
        if _db_conn:
            _db_conn.commit()
    except Exception:
        pass

def cache_close():
    global _db_conn
    if _db_conn:
        try:
            _db_conn.commit()
            _db_conn.close()
        except Exception:
            pass
        _db_conn = None

# ── Utilities ─────────────────────────────────────────────────────────────

def ascii_norm(s):
    return unicodedata.normalize("NFD", s).encode("ascii","ignore").decode().upper()

def _tag_local(tag):
    return tag.split("}")[-1] if "}" in tag else tag

# ── Dump download ─────────────────────────────────────────────────────────

def download_dump(code):
    url  = dump_url(code)
    dest = dump_path(code)
    name = LANGUAGES[code]["name"]

    if os.path.exists(dest):
        size_mb = os.path.getsize(dest) // (1024*1024)
        print(f"  Dump already exists: {dest}  ({size_mb} MB)")
        resp = input("  Download again? [y/N] ").strip().lower()
        if resp != "y":
            return dest

    print(f"\n  Downloading {name} Wiktionary dump...")
    print(f"  URL: {url}")
    print(f"  Destination: {dest}\n")

    try:
        r = requests.get(url, headers=UA, stream=True, timeout=60)
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done  = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024*1024):
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    mb  = done  // (1024*1024)
                    tmb = total // (1024*1024)
                    bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                    print(f"\r  [{bar}] {pct}%  {mb}/{tmb} MB   ",
                          end="", flush=True)
        print(f"\n  ✓ Download complete: {dest}")
        return dest
    except Exception as e:
        print(f"\n  ✗ Download error: {e}")
        if os.path.exists(dest):
            os.remove(dest)
        sys.exit(1)

# ── Dump diagnostics ──────────────────────────────────────────────────────

def dump_diagnostics(filename, n=5):
    """
    Shows the title and first 400 characters of wikitext for the first
    N namespace-0 articles that pass the simple-word filter.
    Useful for verifying the actual dump format.
    """
    print(f"\n{'='*65}")
    print(f"  Dump diagnostics: {os.path.basename(filename)}")
    print(f"  (first {n} simple-word articles in namespace 0)")
    print(f"{'='*65}\n")

    if filename.endswith(".bz2"):
        fh = bz2.open(filename, "rb")
    else:
        fh = open(filename, "rb")

    found         = 0
    current_title = None
    current_ns    = None

    try:
        for event, elem in ET.iterparse(fh, events=("end",)):
            local = _tag_local(elem.tag)
            if local == "title":
                current_title = elem.text or ""
                elem.clear(); continue
            if local == "ns":
                current_ns = elem.text
                elem.clear(); continue
            if local == "text":
                if current_ns != "0" or not current_title:
                    elem.clear(); continue
                t = current_title
                if (" " in t or ":" in t or "/" in t
                        or not unicodedata.normalize("NFD", t)
                            .encode("ascii","ignore").decode()
                            .replace("-","").isalpha()):
                    elem.clear(); continue
                wt = (elem.text or "")[:500]
                print(f"  Title  : {t}")
                print(f"  Wikitext (first 400 chars):")
                print(f"  {repr(wt[:400])}")
                # Test parse
                entry = parse_entry(t, elem.text or "")
                print(f"  parse_entry() → {'✓ entry found' if entry else '✗ no definition'}")
                print()
                found += 1
                elem.clear()
                if found >= n:
                    break
            elif local == "page":
                current_title = None
                current_ns    = None
                elem.clear()
    finally:
        fh.close()

    if found == 0:
        print("  ✗ No simple-word articles found.")
        print("  Check that the dump is correct and not corrupted.")
    print(f"{'='*65}\n")

# ── XML dump parser ───────────────────────────────────────────────────────

def parse_dump(filename, letters_filter=None, prefixes2_filter=None,
               prefixes3_filter=None):
    """
    Reads the XML dump (bz2 or xml) and groups definitions by 3-letter prefix.
    Uses SQLite cache to avoid reprocessing already-seen articles.
    """
    stats  = {"total": 0, "with_def": 0, "no_def": 0,
              "filtered": 0, "cache_hit": 0}
    groups = defaultdict(list)

    if filename.endswith(".bz2"):
        fh = bz2.open(filename, "rb")
    else:
        fh = open(filename, "rb")

    total_size = os.path.getsize(filename)
    last_pct   = -1
    commit_n   = 0   # counter for periodic commits

    print(f"  Processing dump: {os.path.basename(filename)}")

    try:
        current_title = None
        current_ns    = None

        for event, elem in ET.iterparse(fh, events=("end",)):
            local = _tag_local(elem.tag)

            if local == "title":
                current_title = elem.text or ""
                elem.clear(); continue

            if local == "ns":
                current_ns = elem.text
                elem.clear(); continue

            if local == "text":
                if current_ns != "0" or not current_title:
                    elem.clear(); continue

                title = current_title
                tn    = ascii_norm(title)

                # Filter out compound words, namespaces, etc.
                if (" " in title or ":" in title or "/" in title
                        or not unicodedata.normalize("NFD", title)
                            .encode("ascii","ignore").decode()
                            .replace("-","").isalpha()):
                    elem.clear(); continue

                # Filter by letter/prefix
                if letters_filter and tn[0] not in letters_filter:
                    stats["filtered"] += 1
                    elem.clear(); continue
                if prefixes2_filter and tn[:2] not in prefixes2_filter:
                    stats["filtered"] += 1
                    elem.clear(); continue

                stats["total"] += 1

                # Cache hit
                cached = cache_get(title)
                if cached is not None:
                    stats["cache_hit"] += 1
                    if cached:   # has entry
                        pref3 = tn[:3] if len(tn) >= 3 else tn.ljust(3,"_")
                        if not prefixes3_filter or pref3 in prefixes3_filter:
                            groups[pref3].append(cached)
                        stats["with_def"] += 1
                    else:
                        stats["no_def"] += 1
                    elem.clear(); continue

                # Parse wikitext
                wikitext = elem.text or ""
                entry    = parse_entry(title, wikitext)
                cache_set(title, entry if entry else {})

                commit_n += 1
                if commit_n % 500 == 0:
                    cache_commit()

                if entry:
                    pref3 = tn[:3] if len(tn) >= 3 else tn.ljust(3,"_")
                    if not prefixes3_filter or pref3 in prefixes3_filter:
                        groups[pref3].append(entry)
                    stats["with_def"] += 1
                else:
                    stats["no_def"] += 1

                elem.clear()

            elif local == "page":
                try:
                    pos = fh.tell() if hasattr(fh, "tell") else 0
                    pct = min(99, pos * 100 // total_size)
                    if pct != last_pct:
                        print(f"\r  Processed: {pct}%  "
                              f"({stats['total']} articles · "
                              f"{stats['with_def']} defs · "
                              f"{stats['cache_hit']} cache)   ",
                              end="", flush=True)
                        last_pct = pct
                except Exception:
                    pass
                current_title = None
                current_ns    = None
                elem.clear()

    finally:
        cache_commit()
        fh.close()

    print(f"\r  Processed: 100%  "
          f"({stats['total']} articles · "
          f"{stats['with_def']} defs · "
          f"{stats['cache_hit']} cache)   ")
    return groups, stats

# ── Wikitext parser ───────────────────────────────────────────────────────

def parse_entry(word, text):
    """
    Extracts grammatical class and definitions from wikitext for the active language.
    section_re supports multiple language heading formats:
      ={{-pt-}}=  ==Português==  == {{língua|pt}} ==
    Section end: next section at the same or higher heading level.
    """
    section_re = CFG["section_re"]

    # Find the start of the language section
    m = re.search(rf"(?:{section_re})", text, re.IGNORECASE)
    if not m:
        return None

    # Heading level (count leading = signs)
    heading = m.group(0)
    level   = len(heading) - len(heading.lstrip("="))
    level   = max(1, level)

    # Block: use section_end_re if defined (e.g. GL uses templates as markers)
    # otherwise use = level as delimiter (PT/EN/FR/ES...)
    rest           = text[m.end():]
    end_re_override = CFG.get("section_end_re")
    if end_re_override:
        m_end = re.search(end_re_override, rest)
    else:
        end_re = r"\n" + "={1," + str(level) + r"}[^=]"
        m_end  = re.search(end_re, rest)
    block = rest[:m_end.start()] if m_end else rest

    # Grammatical class
    # Mode 1 (PT/EN/FR...): ==Class== or =={{Class|pt}}==
    # Mode 2 (ES): heading via template {{sustantivo|es|...}}
    word_class = ""
    classes_re = "|".join(re.escape(c) for c in CFG["classes"])
    m_cl = re.search(
        rf"==+\s*(?:({classes_re})|\{{\{{(?:{classes_re})\|[^}}]*\}}\}})\s*==+",
        block, re.IGNORECASE
    )
    if m_cl:
        word_class = next((g for g in (m_cl.groups() or []) if g), "").lower()
    elif CFG.get("class_re"):
        m_cl2 = re.search(CFG["class_re"], block, re.IGNORECASE)
        if m_cl2:
            word_class = m_cl2.group(1).lower()

    # Gender
    gender = ""
    g_re   = CFG.get("gender_re")
    if g_re:
        mg = re.search(g_re, block, re.IGNORECASE)
        if mg:
            gender = next((g for g in (mg.groups() or []) if g), "")

    # Definitions
    # Standard format (PT/EN/FR...): # lines (not #* examples or #: sub-defs)
    # ES format: ;N: definition
    defs   = []
    def_re = CFG.get("def_re")   # language-specific alternative pattern

    for ln in block.splitlines():
        ln = ln.strip()
        if def_re and re.match(def_re, ln):
            d = clean_text(re.sub(def_re, "", ln).strip())
            if d and len(d) > 4:
                defs.append(d)
        elif re.match(r"^#[^*:#]", ln):
            d = clean_text(ln[1:].strip())
            if d and len(d) > 4:
                defs.append(d)

    if not defs:
        return None

    return {"p": word, "c": word_class, "g": gender, "d": defs[:3]}


def clean_text(t):
    # Language-specific substitutions (e.g. GL: {{Gl|X}} → X)
    for pattern, repl in CFG.get("clean_extra_re", []):
        t = re.sub(pattern, repl, t)
    # Wikilinks [[dest|text]] → text, [[word]] → word
    t = re.sub(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]", r"\1", t)
    # Remove remaining templates
    t = re.sub(r"\{\{[^}]*\}\}", "", t)
    t = re.sub(r"'{2,3}", "", t)
    t = re.sub(r"<ref[^>]*>.*?</ref>", "", t, flags=re.DOTALL)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^[;:\s]+", "", t)
    return t

# ── CSS and HTML ──────────────────────────────────────────────────────────

CSS = """
body { font-family: sans-serif; margin: 0.7em; }
h1  { font-size: 1.1em; text-align: center; margin-bottom: 0.3em; }
h2  { font-size: 1.0em; margin-top: 1.2em; margin-bottom: 0.2em;
      border-bottom: 1px solid #ddd; padding-bottom: 0.15em; color: #444; }
.e  { margin: 0.3em 0 0.45em 0; }
.p  { font-weight: bold; font-size: 1.05em; }
.g  { font-size: 0.78em; color: #777; margin-left: 0.3em; }
.d  { font-size: 0.9em; line-height: 1.5; margin: 0.05em 0 0.05em 0.5em; }
.n  { font-size: 0.78em; color: #aaa; margin-right: 0.2em; }
hr  { border: none; border-top: 1px solid #eee; margin: 0.5em 0; }
small { color: #aaa; font-size: 0.75em; }
"""

def entry_html(e):
    word       = e["p"]
    word_class = e.get("c","")
    gender     = e.get("g","")
    defs       = e.get("d",[])
    label      = f"{word_class} {gender}." if gender else (f"{word_class}." if word_class else "")
    sg         = f'<span class="g">({label})</span>' if label else ""
    if len(defs) == 1:
        return (f'<div class="e"><span class="p">{word}</span>'
                f'{sg} <span class="d">{defs[0]}</span></div>\n')
    h = [f'<div class="e"><span class="p">{word}</span>{sg}']
    for i, d in enumerate(defs, 1):
        h.append(f'<div class="d"><span class="n">{i}.</span>{d}</div>')
    h.append("</div>")
    return "\n".join(h) + "\n"


def xhtml_page(title, body):
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{CFG.get('code','en')}">
<head><meta charset="utf-8"/>
<title>{title}</title>
<link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>{body}</body>
</html>"""

# ── EPUB generation ───────────────────────────────────────────────────────

def generate_epub(path, prefix3, entries):
    code  = CFG["code"]
    name  = CFG["name"]
    title = f"Dict {name} — {prefix3}"
    uid   = f"dict-{code}-{prefix3.lower()}"

    groups = defaultdict(list)
    for e in entries:
        pn      = ascii_norm(e["p"])
        fourth  = pn[3] if len(pn) >= 4 else "_"
        groups[fourth].append(e)
    groups_sorted = sorted(groups.items())

    pages  = {}
    manids = []
    navpts = []

    index_html = (f"<h1>{title}</h1><hr/>\n"
                  f"<p><small>{len(entries)} entries · "
                  f"Wiktionary {name} · {date.today().strftime('%Y-%m-%d')}"
                  f"</small></p><hr/>\n")
    for fourth, group in groups_sorted:
        p4 = (prefix3 + fourth).upper() if fourth != "_" else prefix3.upper()
        index_html += f'<p><b>{p4}</b><span class="g"> — {len(group)}</span></p>\n'

    pages["i0"] = ("i.html", xhtml_page(title, index_html))
    manids.append(("i0", "i.html"))
    navpts.append(("n0", "i.html", f"{prefix3} — index"))

    for i, (fourth, group) in enumerate(groups_sorted):
        p4    = (prefix3 + fourth).upper() if fourth != "_" else prefix3.upper()
        pid   = f"c{i:03d}"
        fname = f"{pid}.html"
        body  = f'<h2>{p4}</h2>\n' + "".join(entry_html(e) for e in group)
        pages[pid] = (fname, xhtml_page(p4, body))
        manids.append((pid, fname))
        navpts.append((f"n{i+1}", fname, p4))

    manifest = "\n    ".join(
        f'<item id="{pid}" href="{fn}" media-type="application/xhtml+xml"/>'
        for pid, fn in manids
    )
    manifest += '\n    <item id="css" href="style.css" media-type="text/css"/>'
    manifest += '\n    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    spine   = "\n    ".join(f'<itemref idref="{pid}"/>' for pid, _ in manids)
    nav_xml = "\n    ".join(
        f'<navPoint id="{nid}" playOrder="{i}">'
        f'<navLabel><text>{label}</text></navLabel>'
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
    <dc:title>{title}</dc:title>
    <dc:language>{code}</dc:language>
    <dc:identifier id="uid">{uid}</dc:identifier>
    <dc:source>https://{CFG['wiki']}.wiktionary.org</dc:source>
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
  <docTitle><text>{title}</text></docTitle>
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
        for pid, (fname, html) in pages.items():
            z.writestr(f"OEBPS/{fname}", html)

    return os.path.getsize(path) // 1024, len(groups_sorted)

# ── API (only for --update-since) ────────────────────────────────────────

def api_get(params, description="request"):
    params_c = {**params, "maxlag": str(MAXLAG)}
    wait     = 2
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(API_URL, params=params_c, headers=UA, timeout=30)
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 0))
                w = min(max(retry_after, wait), 120)
                if retry_after > 120:
                    print(f"\n    ⚠  Retry-After={retry_after}s ignored (cap=120s)")
                print(f"\n    ⏸  429 — waiting {w}s (attempt {attempt}/{MAX_RETRIES})")
                time.sleep(w)
                wait = min(wait * 2, 120)
                continue
            if r.status_code in (503, 504):
                time.sleep(wait); wait = min(wait*2, 120); continue
            r.raise_for_status()
            data = r.json()
            if "error" in data and data["error"].get("code") == "maxlag":
                lag  = data["error"].get("lag", wait)
                time.sleep(max(int(lag), wait))
                wait = min(wait*2, 120)
                continue
            return data
        except requests.exceptions.ConnectionError:
            time.sleep(wait); wait = min(wait*2, 120)
        except requests.exceptions.Timeout:
            time.sleep(wait); wait = min(wait*2, 120)
        except Exception as e:
            print(f"\n    ✗ Error ({description}): {e}"); return None
    return None

# ── Mode: list languages ──────────────────────────────────────────────────

def mode_list_languages():
    print(f"\n{'='*65}")
    print(f"  Supported languages")
    print(f"{'='*65}")
    for code, cfg in LANGUAGES.items():
        dest   = dump_path(code)
        status = "✓ local" if os.path.exists(dest) else "— not downloaded"
        print(f"  {code}  {cfg['name']:<14}  {status}")
        print(f"       {dump_url(code)}")
    print(f"\n  Usage: python3 {os.path.basename(sys.argv[0])} -l es --download-dump")
    print(f"{'='*65}\n")

# ── Mode: clear empty entries ─────────────────────────────────────────────

def mode_clear_empty():
    print(f"\n{'='*65}")
    print(f"  Mode: clear entries without definition  [{CFG['name']}]")
    print(f"{'='*65}\n")
    try:
        before = _db().execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        _db().execute("DELETE FROM cache WHERE content=''")
        cache_commit()
        after   = _db().execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        deleted = before - after
        print(f"  Cache entries       : {before}")
        print(f"  Entries without def.: {deleted}  (deleted)")
        print(f"  Entries kept        : {after}")
        print(f"\n  Run the script again to reprocess the dump.")
    except Exception as e:
        print(f"  ✗ Error: {e}")
    finally:
        cache_close()
    print(f"{'='*65}\n")

# ── Mode: update since date ───────────────────────────────────────────────

def mode_update_since(date_str, letters_filter=None):
    from datetime import datetime
    try:
        dt       = datetime.strptime(date_str, "%Y-%m-%d")
        date_iso = dt.strftime("%Y-%m-%dT00:00:00Z")
    except ValueError:
        print(f"  ✗ Invalid date: '{date_str}' (format: YYYY-MM-DD)")
        return

    print(f"\n{'='*65}")
    print(f"  Mode: update since {date_str}  [{CFG['name']}]")
    if letters_filter:
        print(f"  Letter filter: {' '.join(letters_filter)}")
    print(f"{'='*65}\n")

    modified_words = set()
    params = {
        "action":       "query",
        "list":         "allrevisions",
        "arvstart":     date_iso,
        "arvdir":       "newer",
        "arvnamespace": "0",
        "arvlimit":     "500",
        "arvprop":      "title",
        "format":       "json",
    }
    page = 1
    print(f"  Fetching modified pages via API...", end=" ", flush=True)
    while True:
        dr = api_get(params, f"allrevisions page.{page}")
        if not dr: break
        for rev in dr.get("query", {}).get("allrevisions", []):
            t  = rev.get("title","")
            tn = ascii_norm(t)
            if (" " not in t and ":" not in t and "/" not in t
                    and unicodedata.normalize("NFD", t)
                        .encode("ascii","ignore").decode()
                        .replace("-","").isalpha()):
                if letters_filter and tn[0] not in letters_filter:
                    continue
                modified_words.add(t)
        cont = dr.get("continue", {})
        if "arvcontinue" in cont:
            params["arvcontinue"] = cont["arvcontinue"]
            page += 1; time.sleep(PAUSE)
        else:
            break

    print(f"{len(modified_words)} pages")
    if not modified_words:
        print(f"  No modifications found.")
        cache_close(); print(f"{'='*65}\n"); return

    # Delete from SQLite cache
    cache_deleted = 0
    for w in modified_words:
        res = _db().execute("DELETE FROM cache WHERE key=?", (w,))
        cache_deleted += res.rowcount
    cache_commit()

    # Delete affected EPUBs
    affected_prefixes = set()
    for w in modified_words:
        wn = ascii_norm(w)
        if len(wn) >= 3:
            affected_prefixes.add(wn[:3])

    epubs_deleted = 0
    for pref3 in affected_prefixes:
        epub = os.path.join(OUTPUT_FOLDER, pref3[0], pref3[:2],
                            f"DICT_{pref3}.epub")
        if os.path.exists(epub):
            os.remove(epub); epubs_deleted += 1

    sample = ", ".join(sorted(affected_prefixes)[:12])
    if len(affected_prefixes) > 12: sample += "..."

    print(f"\n  Modified pages            : {len(modified_words)}")
    print(f"  Cache entries deleted     : {cache_deleted}")
    print(f"  EPUBs deleted             : {epubs_deleted}")
    print(f"  Affected prefixes         : {sample}")
    print(f"\n  Run the script again to regenerate from the dump.")
    cache_close()
    print(f"{'='*65}\n")

# ── Main ──────────────────────────────────────────────────────────────────

def main(dump_file, letters_proc, prefixes2, prefixes3):
    code = CFG["code"]
    name = CFG["name"]

    letters_f   = {a for a in letters_proc} if letters_proc != CFG["letters"] else None
    prefixes2_f = set(prefixes2) if prefixes2 else None
    prefixes3_f = set(prefixes3) if prefixes3 else None

    print(f"\n{'='*65}")
    print(f"  Dictionary {name}  [{code}]")
    print(f"  Dump     : {dump_file}")
    print(f"  Cache    : {CACHE_DB}  (SQLite)")
    print(f"  Structure: dictionaries/{code}/X/XY/DICT_XYZ.epub")
    print(f"  Chapters : XYZA, XYZB... (4th letter) via NCX")
    if letters_f:
        print(f"  Letters  : {' '.join(sorted(letters_f))}")
    if prefixes2_f:
        print(f"  Prefixes : {' '.join(sorted(prefixes2_f))}")
    if prefixes3_f:
        print(f"  Prefixes : {' '.join(sorted(prefixes3_f))}")
    print(f"{'='*65}\n")

    t_start = time.time()

    try:
        groups, dump_stats = parse_dump(
            dump_file,
            letters_filter   = letters_f,
            prefixes2_filter = prefixes2_f,
            prefixes3_filter = prefixes3_f,
        )
    except KeyboardInterrupt:
        print(f"\n\n  Interrupted by user. Saving cache...")
        cache_commit()
        cache_close()
        print(f"  Cache saved. Run again to resume.")
        sys.exit(0)
    except Exception as e:
        print(f"\n  ✗ Error during parse: {e}")
        cache_commit()
        cache_close()
        raise

    cache_commit()

    t_parse = time.time() - t_start
    print(f"\n  Parse completed in {t_parse:.1f}s")

    # ── Generate EPUBs ────────────────────────────────────────────────────
    epubs_generated = 0
    epubs_skipped   = 0
    entries_total   = 0

    for pref3 in sorted(groups):
        entries = groups[pref3]
        if not entries: continue

        letter = pref3[0]
        pref2  = pref3[:2]
        folder = os.path.join(OUTPUT_FOLDER, letter, pref2)
        os.makedirs(folder, exist_ok=True)
        path   = os.path.join(folder, f"DICT_{pref3}.epub")

        if os.path.exists(path):
            epubs_skipped += 1
            continue

        kb, n_chapters = generate_epub(path, pref3, entries)
        print(f"  [{pref3}] {len(entries)} entries · {n_chapters} ch. · {kb} KB")
        epubs_generated += 1
        entries_total   += len(entries)

    # ── Summary ───────────────────────────────────────────────────────────
    t_total  = time.time() - t_start
    hours    = int(t_total // 3600)
    minutes  = int((t_total % 3600) // 60)
    seconds  = int(t_total % 60)

    total_proc = dump_stats["with_def"] + dump_stats["no_def"]
    def pct(n): return f"{n*100//total_proc}%" if total_proc else "—"

    print(f"\n{'='*65}")
    print(f"  SUMMARY — {name} [{code}]")
    print(f"{'='*65}")
    print(f"  Total time         : {hours}h {minutes}m {seconds}s")
    print(f"    of which parse   : {t_parse:.1f}s")
    print(f"")
    print(f"  DUMP ARTICLES")
    print(f"    Total processed  : {dump_stats['total']}")
    print(f"    Cache hits       : {dump_stats['cache_hit']}")
    print(f"    With definition  : {dump_stats['with_def']}  ({pct(dump_stats['with_def'])})")
    print(f"    Without definition: {dump_stats['no_def']}  ({pct(dump_stats['no_def'])})")
    print(f"    Filtered         : {dump_stats['filtered']}")
    print(f"")
    print(f"  EPUBs")
    print(f"    Generated        : {epubs_generated}")
    print(f"    Skipped (exist.) : {epubs_skipped}")
    print(f"    Total on disk    : {epubs_generated + epubs_skipped}")
    print(f"    New entries      : {entries_total}")
    print(f"")
    print(f"  Folder             : {os.path.abspath(OUTPUT_FOLDER)}/")
    if dump_stats["with_def"] == 0:
        print(f"\n  ⚠  No definitions found!")
        print(f"  Run with -v to diagnose the dump format:")
        print(f"     python3 {os.path.basename(sys.argv[0])} -l {code} -v -d {dump_file}")
    elif dump_stats["no_def"] > 0:
        print(f"\n  💡 To retry entries without definition:")
        print(f"     python3 {os.path.basename(sys.argv[0])} -l {code} -c")
    print(f"{'='*65}\n")

    cache_close()

# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        prog=os.path.basename(sys.argv[0]),
        description="Builds dictionary EPUBs from Wiktionary dumps.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s -l pt --download-dump    # download PT dump\n"
            "  %(prog)s -l pt                    # generate all (PT)\n"
            "  %(prog)s -l pt -v                 # diagnose PT dump\n"
            "  %(prog)s -l es --download-dump    # download ES dump\n"
            "  %(prog)s -l es A B                # Spanish, letters A B\n"
            "  %(prog)s -l en AB ABA             # English, prefixes\n"
            "  %(prog)s -i                       # list languages\n"
            "  %(prog)s -l pt -c                 # clear entries without def.\n"
            "  %(prog)s -l pt -a 2025-06-01      # update since date\n"
        ),
    )
    ap.add_argument("-l","--lang",
        metavar="CODE", default="pt",
        help="Language code (default: pt). Available: pt es en fr de it ca gl")
    ap.add_argument("-i","--list-languages",
        action="store_true", help="List available languages and exit.")
    ap.add_argument("-d","--dump",
        metavar="FILE",
        help="Path to the XML/.bz2 dump file (default: dumps/ folder).")
    ap.add_argument("--download-dump",
        action="store_true", help="Download the language dump and exit.")
    ap.add_argument("-v","--verbose",
        action="store_true",
        help="Diagnostic mode: show real dump format and test parse_entry().")
    ap.add_argument("-c","--clear-empty",
        action="store_true",
        help="Delete entries without definition from the cache so they are retried.")
    ap.add_argument("-a","--update-since",
        metavar="YYYY-MM-DD",
        help="Delete cache/EPUBs for pages modified since this date (uses API).")
    ap.add_argument("prefixes",
        nargs="*", metavar="LETTER_OR_PREFIX",
        help=(
            "Letters (A), 2-letter prefixes (AB) or 3-letter prefixes (ABA).\n"
            "Without arguments: process everything.\n"
            "With -a: filter the update by letters."
        ))

    args = ap.parse_args()

    if args.list_languages:
        mode_list_languages()
        sys.exit(0)

    configure_language(args.lang.lower())

    if args.download_dump:
        download_dump(args.lang.lower())
        sys.exit(0)

    # Determine dump file
    dump_file = args.dump or dump_path(args.lang.lower())
    if not args.clear_empty and not args.update_since:
        if not os.path.exists(dump_file):
            print(f"\n  ✗ Dump not found: {dump_file}")
            print(f"  Download it first with:")
            print(f"     python3 {os.path.basename(sys.argv[0])} "
                  f"-l {args.lang} --download-dump\n")
            sys.exit(1)

    if args.clear_empty:
        mode_clear_empty()
        sys.exit(0)

    if args.update_since:
        letters_f = [a.upper() for a in args.prefixes
                     if len(a) == 1 and a.isalpha()]
        mode_update_since(args.update_since,
                          letters_f if letters_f else None)
        sys.exit(0)

    if args.verbose:
        dump_diagnostics(dump_file)
        sys.exit(0)

    tokens      = [a.upper() for a in args.prefixes
                   if a.replace("-","").isalpha()]
    letters_proc = sorted({a for a in tokens if len(a) == 1}) or CFG["letters"]
    prefixes2    = sorted({a for a in tokens if len(a) == 2})
    prefixes3    = sorted({a for a in tokens if len(a) == 3})

    main(dump_file, letters_proc, prefixes2, prefixes3)
