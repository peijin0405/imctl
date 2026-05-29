"""
pdf_investor_extractor.py
Step 6: Extract VC firm names from PDF reports and HTML pages, then enrich
each with Exa to get website, description, contacts, and portfolio.

Sources:
  - NVCA 2025 Yearbook PDF  → Board of Directors page (~30 elite VC firms)
  - CTVC Running List HTML  → ~60 climate-tech VC firms (greentech)
  - CTVC Investors HTML     → additional climate/energy VC names (energy)

Usage:
  python scraper/pdf_investor_extractor.py [--test] [--resume] [--parse-only]
                                            [--sectors greentech energy healthcare]
                                            [--stats]
"""

import argparse
import concurrent.futures
import hashlib
import json
import logging
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    from exa_py import Exa
except ImportError:
    Exa = None

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

EXA_API_KEY = os.getenv("EXA_API_KEY")
WEB_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "web", "investors.json")
PDF_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "pdfs")
PROGRESS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "pdf_progress.json")
EXA_TIMEOUT = 45

# ── Source definitions ────────────────────────────────────────────────────────

SOURCES = {
    "nvca_yearbook": {
        "url": "https://nvca.org/wp-content/uploads/2025/03/2025-NVCA-Yearbook.pdf",
        "filename": "2025-NVCA-Yearbook.pdf",
        "type": "pdf",
        "sector": "general",
        "extractor": "nvca_board",
        "description": "NVCA 2025 Yearbook — Board of Directors page (~30 top US VC firms)",
    },
    "ctvc_running_list": {
        "url": "https://www.ctvc.co/a-running-list-of-climate-tech-vcs/",
        "filename": "ctvc-running-list.html",
        "type": "html",
        "sector": "greentech",
        "extractor": "ctvc",
        "description": "CTVC running list of climate tech VCs (~60 firms)",
    },
    "ctvc_who_investors": {
        "url": "https://www.ctvc.co/who-are-the-climate-tech-investors/",
        "filename": "ctvc-who-investors.html",
        "type": "html",
        "sector": "energy",
        "extractor": "ctvc",
        "description": "CTVC climate tech investor categories",
    },
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

AGGREGATOR_DOMAINS = [
    "crunchbase.com", "pitchbook.com", "linkedin.com", "wikipedia.org",
    "twitter.com", "x.com", "bloomberg.com", "techcrunch.com",
    "forbes.com", "reuters.com", "wsj.com", "cbinsights.com",
]

# ── Field vocabulary ──────────────────────────────────────────────────────────

FIRM_SUFFIXES = [
    "ventures", "venture", "capital", "partners", "fund", "funds",
    "investments", "equity", "management", "advisors", "advisers",
    "holdings", "group", "associates", "asset", "growth", "health",
    "bio", "tech", "energy", "innovation", "innovations", "labs",
    "solutions", "technologies",
]

NOISE_TOKENS = [
    "click", "read more", "learn more", "sign up", "subscribe",
    "terms", "privacy", "©", "@", "http", "www.", "table of",
    "figure", "chart", "source:", "note:", "total", "%", "$",
    "billion", "million", "q1 ", "q2 ", "q3 ", "q4 ",
]

SECTOR_KEYWORDS: dict[str, list[str]] = {
    "ai_apps": [
        "artificial intelligence", "machine learning", "deep learning",
        "generative ai", "llm", "large language model", "enterprise ai",
        "foundation model", "nlp", "natural language", "mlops",
    ],
    "ai_hardware": [
        "robotics", "robot", "autonomous vehicle", "self-driving",
        "industrial automation", "embodied ai", "edge ai", "humanoid",
        "drone", "lidar", "autonomous systems",
    ],
    "semiconductor": [
        "semiconductor", "chip", "fabless", "eda", "fpga", "gpu",
        "sic", "gan", "integrated circuit", "wafer", "foundry",
        "photonics", "quantum computing",
    ],
    "healthcare": [
        "health", "healthcare", "biotech", "biotechnology",
        "pharmaceutical", "pharma", "life sciences", "medical",
        "clinical", "therapeutics", "drug", "genomics", "diagnostics",
        "medtech", "digital health", "biopharma", "oncology",
    ],
    "edtech": [
        "education", "edtech", "e-learning", "learning", "workforce",
        "higher education", "academic", "online learning",
    ],
    "fintech": [
        "fintech", "financial technology", "payments", "payment",
        "blockchain", "crypto", "insurtech", "wealthtech", "neobank",
        "lending", "regtech", "defi", "digital banking",
    ],
    "greentech": [
        "cleantech", "clean tech", "environmental", "sustainability",
        "sustainable", "carbon", "recycling", "circular economy",
        "esg", "impact investing", "climate tech", "net zero",
    ],
    "energy": [
        "clean energy", "renewable energy", "solar", "wind energy",
        "battery", "energy storage", "hydrogen", "nuclear",
        "electric vehicle", " ev ", "decarbonization", "power grid",
        "energy transition",
    ],
}

STAGE_KEYWORDS: dict[str, list[str]] = {
    "Pre-Seed": ["pre-seed", "pre seed"],
    "Seed": ["seed stage", "seed fund", "seed capital", "early stage"],
    "Series A": ["series a"],
    "Series B": ["series b"],
    "Series C+": ["series c", "late stage", "pre-ipo"],
    "Growth": ["growth equity", "growth capital"],
    "Buyout": ["buyout", "lbo"],
    "Multi-Stage": ["multi-stage", "all stages"],
}

CITY_STATE_MAP: dict[str, tuple[str, str]] = {
    "san francisco": ("CA", "San Francisco"),
    "silicon valley": ("CA", "Silicon Valley"),
    "palo alto": ("CA", "Palo Alto"),
    "menlo park": ("CA", "Menlo Park"),
    "los angeles": ("CA", "Los Angeles"),
    "san jose": ("CA", "San Jose"),
    "san diego": ("CA", "San Diego"),
    "new york": ("NY", "New York"),
    "boston": ("MA", "Boston"),
    "cambridge": ("MA", "Cambridge"),
    "chicago": ("IL", "Chicago"),
    "austin": ("TX", "Austin"),
    "dallas": ("TX", "Dallas"),
    "houston": ("TX", "Houston"),
    "seattle": ("WA", "Seattle"),
    "denver": ("CO", "Denver"),
    "miami": ("FL", "Miami"),
    "atlanta": ("GA", "Atlanta"),
    "washington dc": ("DC", "Washington"),
    "portland": ("OR", "Portland"),
    "minneapolis": ("MN", "Minneapolis"),
    "nashville": ("TN", "Nashville"),
    "phoenix": ("AZ", "Phoenix"),
    "new haven": ("CT", "New Haven"),
    "princeton": ("NJ", "Princeton"),
    "philadelphia": ("PA", "Philadelphia"),
    "raleigh": ("NC", "Raleigh"),
    "salt lake city": ("UT", "Salt Lake City"),
}

US_GEO_TERMS = [
    "united states", " usa", " u.s.", "new york", "san francisco",
    "silicon valley", "boston", "los angeles", "chicago", "austin",
    "seattle", "denver", "miami", "atlanta", "washington",
    "menlo park", "palo alto", "cambridge", "san jose", "san diego",
]

FOREIGN_SIGNALS = [
    "london", "berlin", "paris", "beijing", "shanghai",
    "singapore", "tokyo", ".co.uk", ".de", ".fr", ".cn",
]

# Regex patterns
_FOUNDED_RE = re.compile(
    r"\b(?:founded|established|est\.?|since)\s+(?:in\s+)?(\d{4})\b", re.IGNORECASE
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
_LINKEDIN_RE = re.compile(r"linkedin\.com/in/[\w-]+")
_PERSON_TITLE_RE = re.compile(
    r"\b([A-Z][a-z]{1,20}(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]{1,20})\s*[,\-–]?\s*"
    r"(General\s+Partner|Managing\s+(?:Partner|Director)|Partner|GP|"
    r"Co-Founder|Founder|CEO|CTO|President|MD|VP|Principal|Director|"
    r"Investment\s+Partner|Operating\s+Partner|Senior\s+Partner|Venture\s+Partner)"
)

# ── Exa client ────────────────────────────────────────────────────────────────

_exa_client = None


def _get_exa():
    global _exa_client
    if _exa_client is None:
        if Exa is None:
            raise RuntimeError("exa_py not installed")
        _exa_client = Exa(EXA_API_KEY)
    return _exa_client


def _exa_call(fn):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn)
        try:
            return future.result(timeout=EXA_TIMEOUT)
        except concurrent.futures.TimeoutError:
            log.warning("Exa call timed out after %ds", EXA_TIMEOUT)
            return None

# ── Name validation ───────────────────────────────────────────────────────────

def is_valid_firm_name(text: str) -> bool:
    if not text or not isinstance(text, str):
        return False
    text = text.strip()
    if not (5 <= len(text) <= 80):
        return False
    words = text.split()
    if not (2 <= len(words) <= 7):
        return False
    if not any(s in text.lower() for s in FIRM_SUFFIXES):
        return False
    if any(n in text.lower() for n in NOISE_TOKENS):
        return False
    if not any(w[0].isupper() for w in words if w and w[0].isalpha()):
        return False
    if text[0].isdigit():
        return False
    return True


def normalize_name(name: str) -> str:
    REMOVE = [
        " llc", " lp", " l.p.", " inc", " ltd", " limited",
        " partners", " capital", " fund", " ventures", " venture",
        " management", " advisors", " advisers", " group",
        " investments", " equity", ",", ".", "-",
    ]
    n = name.lower().strip()
    for r in REMOVE:
        n = n.replace(r, "")
    return n.strip()


def is_duplicate(name: str, hq_state: str | None, existing_norm: dict) -> bool:
    norm = normalize_name(name)
    if norm in existing_norm:
        return True
    for ex_norm, ex_state in existing_norm.items():
        if SequenceMatcher(None, norm, ex_norm).ratio() > 0.90:
            if not hq_state or not ex_state or hq_state == ex_state:
                return True
    return False

# ── Download helpers ──────────────────────────────────────────────────────────

def download_file(url: str, save_path: str) -> bool:
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    if os.path.exists(save_path) and os.path.getsize(save_path) > 1000:
        log.info("  Already exists: %s", os.path.basename(save_path))
        return True
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
        if resp.status_code == 200:
            with open(save_path, "wb") as f:
                f.write(resp.content)
            log.info("  Downloaded: %s (%d KB)",
                     os.path.basename(save_path), os.path.getsize(save_path) // 1024)
            return True
        log.warning("  HTTP %d: %s", resp.status_code, url)
        return False
    except Exception as e:
        log.warning("  Download failed %s: %s", url, e)
        return False

# ── Source-specific extractors ────────────────────────────────────────────────

def extract_nvca_board(pdf_path: str) -> list[str]:
    """
    Extract firm names from NVCA Yearbook Board of Directors page (page 2).
    Page format: 'ALL CAPS PERSON NAME Title Case Firm Name, Role'
    We extract the Title Case firm name from each line, dropping person name prefix.
    """
    if pdfplumber is None:
        log.error("pdfplumber not installed: pip install pdfplumber")
        return []

    # Match: one or more ALL-CAPS words, then a Title Case firm name
    # e.g. "BYRON DEETER Bessemer Venture Partners, Chair"
    # group(1) = "Bessemer Venture Partners"
    allcaps_person_re = re.compile(
        r"^(?:[A-Z]{2,}(?:\s+[A-Z]\.?)?(?:\s+[A-Z]{2,})*\s+)"
        r"([A-Z][a-zA-Z0-9&'\-]+(?:\s+[A-Z][a-zA-Z0-9&'\-]+){1,5})"
    )

    firms: set[str] = set()

    with pdfplumber.open(pdf_path) as pdf:
        log.info("  NVCA PDF: %d pages total", len(pdf.pages))
        text = pdf.pages[1].extract_text() or ""  # page index 1 = page 2
        for line in text.split("\n"):
            line = line.strip()
            m = allcaps_person_re.match(line)
            if m:
                candidate = m.group(1).strip()
                # Strip trailing role labels
                candidate = re.sub(
                    r",?\s*(Chair|Treasurer|At-Large|Elect|LLC|Inc).*$",
                    "", candidate
                ).strip()
                if is_valid_firm_name(candidate):
                    firms.add(candidate)

    result = sorted(firms)
    log.info("  NVCA Board: %d firm names extracted", len(result))
    for f in result:
        log.info("    %s", f)
    return result


def extract_ctvc(html_path: str) -> list[str]:
    """
    Extract VC firm names from CTVC HTML pages.
    CTVC format: lines like "Firm Name: description..." or plain "Firm Name".
    Deduplicates names that appear both with and without trailing colon.
    Filters out startup company names (no VC suffixes in name).
    """
    with open(html_path, "rb") as f:
        soup = BeautifulSoup(f.read(), "lxml")

    for tag in soup(["nav", "footer", "header", "script", "style"]):
        tag.decompose()

    candidates: set[str] = set()
    full_text = soup.get_text(separator="\n")

    for line in full_text.split("\n"):
        line = line.strip()
        # Strip parentheticals like "(prev. New Crop Capital)"
        line = re.sub(r"\s*\([^)]*\)\s*", " ", line).strip()
        # Extract name before colon
        if ":" in line:
            cand = line.split(":")[0].strip()
            if is_valid_firm_name(cand):
                candidates.add(cand)
        if is_valid_firm_name(line):
            candidates.add(line)

    for tag in soup.find_all(["a", "h2", "h3", "h4", "li", "strong", "b"]):
        t = re.sub(r"\s*\([^)]*\)\s*", " ", tag.get_text(strip=True)).strip()
        if is_valid_firm_name(t):
            candidates.add(t)

    # Keep only names that contain a clear VC/fund suffix word
    VC_SUFFIXES = frozenset([
        "ventures", "venture", "capital", "partners", "fund", "funds",
        "investments", "equity", "management", "advisors", "advisers",
        "group", "associates",
    ])

    def has_vc_suffix(name: str) -> bool:
        return any(s in name.lower().split() for s in VC_SUFFIXES)

    # Also reject names that look like sentences or navigation text
    GARBAGE_RE = re.compile(
        r"^(A |An |The |Their |Invest|MIT-|Recommend|Who are|Running list"
        r"|\)|🌏|:|\d)",
        re.IGNORECASE,
    )

    result = sorted(
        c for c in candidates
        if has_vc_suffix(c) and not GARBAGE_RE.match(c)
    )

    log.info("  CTVC: %d firm names extracted", len(result))
    return result

# ── Field extractors ──────────────────────────────────────────────────────────

def extract_sectors(text: str) -> tuple[str, list[str], list[str]]:
    text_lower = text.lower()
    scores: dict[str, int] = {}
    matched: dict[str, list[str]] = {}
    for sector, keywords in SECTOR_KEYWORDS.items():
        hits = [kw for kw in keywords if kw in text_lower]
        if hits:
            scores[sector] = len(hits)
            matched[sector] = hits
    if not scores:
        return "general", [], []
    primary = max(scores, key=scores.get)
    all_sectors = sorted(scores, key=lambda s: scores[s], reverse=True)
    return primary, all_sectors, matched[primary][:10]


def extract_location(text: str) -> tuple[str | None, str | None]:
    tl = text.lower()
    for city, (state, city_name) in CITY_STATE_MAP.items():
        if city in tl:
            return state, city_name
    return None, None


def extract_stages(text: str) -> list[str]:
    tl = text.lower()
    found = [s for s, kws in STAGE_KEYWORDS.items() if any(kw in tl for kw in kws)]
    return found or ["Unknown"]


def extract_description(text: str) -> str | None:
    clean = " ".join(text.split())
    return clean[:300] if len(clean) > 50 else None


def extract_founded_year(text: str) -> int | None:
    for m in _FOUNDED_RE.finditer(text):
        year = int(m.group(1))
        if 1970 <= year <= 2026:
            return year
    return None


def extract_portfolio(text: str) -> list[str]:
    portfolio: list[str] = []
    for pat in [
        r"portfolio (?:companies|investments|include)[:\s]+([A-Z][^.]{10,100})",
        r"invested in ([A-Z][a-zA-Z\s,]{10,80})",
        r"backed (?:companies)?[:\s]+([A-Z][^.]{10,80})",
    ]:
        for match in re.findall(pat, text):
            names = [n.strip() for n in match.split(",") if 2 <= len(n.strip()) <= 40]
            portfolio.extend(names[:5])
    return list(set(portfolio))[:15]


def extract_people(text: str) -> list[dict]:
    people: list[dict] = []
    seen: set[str] = set()
    for m in _PERSON_TITLE_RE.finditer(text):
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        key = name.lower()
        if key not in seen and 2 <= len(name.split()) <= 4:
            seen.add(key)
            people.append({"name": name, "title": m.group(2)})
    return people[:8]


def extract_contacts(text_body: str, highlights: list[str]) -> list[dict]:
    full = text_body + "\n" + "\n".join(highlights)

    emails = [m.group(0) for m in _EMAIL_RE.finditer(full)]
    phones = [m.group(0) for m in _PHONE_RE.finditer(full)]
    linkedins = [
        ("https://" + m.group(0)) if not m.group(0).startswith("http") else m.group(0)
        for m in _LINKEDIN_RE.finditer(full)
    ]

    named: list[dict] = []
    for m in _PERSON_TITLE_RE.finditer(full):
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        if 2 <= len(name.split()) <= 4:
            named.append({"name": name, "title": m.group(2), "pos": m.start()})

    contacts: list[dict] = []
    seen_names: set[str] = set()
    PROX = 200

    for person in named:
        if person["name"].lower() in seen_names:
            continue
        seen_names.add(person["name"].lower())
        pos = person["pos"]

        nearby_email = next((e for e in emails if abs(full.find(e) - pos) <= PROX), None)
        nearby_phone = next((p for p in phones if abs(full.find(p) - pos) <= PROX), None)
        nearby_li = next(
            (li for li in linkedins if abs(full.find(li.replace("https://", "")) - pos) <= PROX),
            None,
        )
        has_contact = any([nearby_email, nearby_phone, nearby_li])
        contacts.append({
            "name": person["name"],
            "title": person["title"],
            "email": nearby_email,
            "phone": nearby_phone,
            "linkedin": nearby_li,
            "confidence": "high" if has_contact else "medium",
        })

    used_emails = {c["email"] for c in contacts if c.get("email")}
    used_phones = {c["phone"] for c in contacts if c.get("phone")}
    used_lis = {c["linkedin"] for c in contacts if c.get("linkedin")}

    for email in emails:
        if email not in used_emails:
            contacts.append({"name": None, "title": None, "email": email,
                             "phone": None, "linkedin": None, "confidence": "low"})
    for phone in phones:
        if phone not in used_phones:
            contacts.append({"name": None, "title": None, "email": None,
                             "phone": phone, "linkedin": None, "confidence": "low"})
    for li in linkedins:
        if li not in used_lis:
            contacts.append({"name": None, "title": None, "email": None,
                             "phone": None, "linkedin": li, "confidence": "low"})

    return contacts[:20]


def infer_investor_type(name: str, text: str) -> str:
    combined = (name + " " + text).lower()
    if any(k in combined for k in ["venture capital", "venture fund", "vc fund"]):
        return "VC"
    if any(k in combined for k in ["private equity", "buyout", "growth equity"]):
        return "PE"
    if "hedge fund" in combined:
        return "Hedge Fund"
    if "family office" in combined:
        return "Family Office"
    if any(k in name.lower() for k in ["venture", "ventures"]):
        return "VC"
    return "VC"

# ── Exa enrichment ────────────────────────────────────────────────────────────

def enrich_with_exa(firm_name: str, sector: str) -> dict | None:
    query = f"{firm_name} venture capital investment United States team partners contact"

    resp = _exa_call(lambda: _get_exa().search_and_contents(
        query,
        num_results=1,
        type="neural",
        exclude_domains=AGGREGATOR_DOMAINS,
        text={"max_characters": 2000},
        highlights={"num_sentences": 6, "highlights_per_url": 3},
    ))

    if resp is None or not resp.results:
        return None

    r = resp.results[0]
    url = r.url or ""
    text_body = r.text or ""
    highlights = r.highlights or []
    full_text = text_body + " " + " ".join(highlights)
    full_lower = full_text.lower()

    if not any(geo in full_lower for geo in US_GEO_TERMS):
        if any(f in url.lower() for f in FOREIGN_SIGNALS):
            return None

    invest_terms = {"invest", "portfolio", "fund", "capital", "venture",
                    "equity", "back", "stage", "startup"}
    if not any(t in full_lower for t in invest_terms):
        return None

    sector_primary, sectors_all, focus = extract_sectors(full_text)
    if sector_primary == "general":
        sector_primary = sector
        sectors_all = [sector]

    hq_state, hq_city = extract_location(full_text)
    portfolio = extract_portfolio(full_text)
    key_people = extract_people(full_text)
    contacts = extract_contacts(text_body, highlights)
    stages = extract_stages(full_text)
    investor_type = infer_investor_type(firm_name, full_text)
    description = extract_description(full_text)
    founded_year = extract_founded_year(full_text)

    website = None
    if url and not any(agg in url for agg in AGGREGATOR_DOMAINS):
        parsed = urlparse(url)
        website = f"{parsed.scheme}://{parsed.netloc}"

    firm_id = "pdf_" + hashlib.md5(
        (firm_name + (hq_state or "")).lower().encode()
    ).hexdigest()[:12]

    return {
        "id": firm_id,
        "name": firm_name.strip(),
        "description": description,
        "investor_type": investor_type,
        "sector": sector_primary,
        "sectors": sectors_all,
        "stage": stages,
        "focus": focus,
        "region": "North America",
        "hq_state": hq_state,
        "hq_city": hq_city,
        "hq_country": "US",
        "us_verified": True,
        "website": website,
        "aum_usd": None,
        "fund_size_usd": None,
        "check_size_min_usd": None,
        "check_size_max_usd": None,
        "key_people": key_people,
        "contacts": contacts,
        "portfolio": portfolio,
        "founded_year": founded_year,
        "sec_crd": None,
        "sec_file_num": None,
        "sec_form_type": None,
        "sec_filing_url": None,
        "data_source": "pdf_report",
        "data_confidence": "high",
        "scraped_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

# ── Main ──────────────────────────────────────────────────────────────────────

EXTRACTOR_MAP = {
    "nvca_board": extract_nvca_board,
    "ctvc": extract_ctvc,
}


def print_stats(data: list[dict]) -> None:
    focused = [d for d in data if d.get("sector") != "general"]
    by_src = Counter(d.get("data_source") for d in data)
    by_sector = Counter(d.get("sector") for d in data)
    print(f"\nTotal records: {len(data)}")
    print(f"Sector-focused: {len(focused)}")
    print("By source:")
    for s, c in by_src.most_common():
        print(f"  {s}: {c}")
    print("By sector (top 10):")
    for s, c in by_sector.most_common(10):
        print(f"  {s}: {c}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sectors", nargs="+", metavar="SECTOR")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--parse-only", action="store_true",
                        help="Extract names from files but skip Exa enrichment")
    parser.add_argument("--test", action="store_true",
                        help="Test mode: extract names, enrich first 5 only")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    with open(WEB_DATA_PATH, encoding="utf-8") as f:
        existing = json.load(f)
    log.info("Loaded %d existing records", len(existing))

    if args.stats:
        print_stats(existing)
        return

    if not args.parse_only and not EXA_API_KEY:
        log.error("EXA_API_KEY not set")
        return

    existing_norm: dict[str, str] = {
        normalize_name(inv.get("name", "")): (inv.get("hq_state") or "")
        for inv in existing
    }

    completed: set[str] = set()
    if args.resume and os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            completed = set(json.load(f).get("completed", []))
        log.info("Resume: %d sources already done", len(completed))

    sector_filter = set(args.sectors) if args.sectors else None
    new_records: list[dict] = []
    sector_stats: Counter = Counter()

    for source_key, src in SOURCES.items():
        if sector_filter and src["sector"] not in sector_filter:
            continue
        if source_key in completed:
            log.info("⏭  Skip (done): %s", source_key)
            continue

        log.info("")
        log.info("=" * 55)
        log.info("Source: %s | sector: %s", source_key, src["sector"])
        log.info("%s", src["description"])
        log.info("=" * 55)

        save_path = os.path.join(PDF_DIR, src["filename"])
        if not download_file(src["url"], save_path):
            log.warning("Download failed, skipping %s", source_key)
            continue

        # Extract firm names using source-specific extractor
        extractor_fn = EXTRACTOR_MAP[src["extractor"]]
        firm_names = extractor_fn(save_path)
        log.info("Candidate firm names: %d", len(firm_names))

        if args.parse_only:
            log.info("--parse-only: skipping Exa enrichment")
            completed.add(source_key)
            continue

        enrich_limit = 5 if args.test else len(firm_names)
        enriched = 0

        for firm_name in firm_names[:enrich_limit]:
            if is_duplicate(firm_name, None, existing_norm):
                log.info("  dup: %s", firm_name)
                continue

            log.info("  🔍 Exa: %s", firm_name)
            record = enrich_with_exa(firm_name, src["sector"])
            time.sleep(0.5)

            if record:
                new_records.append(record)
                existing_norm[normalize_name(firm_name)] = record.get("hq_state") or ""
                sector_stats[src["sector"]] += 1
                enriched += 1
                log.info("    → added: %s [%s]", record["name"], record["sector"])
            else:
                log.info("    → skipped (no US VC match)")

        log.info("Source %s done: +%d new records", source_key, enriched)

        completed.add(source_key)
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump({"completed": list(completed)}, f)

        merged = existing + new_records
        with open(WEB_DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        log.info("Saved: %d total records", len(merged))

    final = existing + new_records
    log.info("")
    log.info("=" * 55)
    log.info("Done. +%d new → %d total", len(new_records), len(final))
    log.info("New by sector:")
    for s, c in sector_stats.most_common():
        log.info("  %-15s +%d", s, c)

    if new_records:
        has_contacts = sum(1 for r in new_records if r.get("contacts"))
        high_conf = sum(
            1 for r in new_records
            if any(c.get("confidence") == "high" for c in (r.get("contacts") or []))
        )
        log.info("Contacts: %d/%d records have contacts, %d high-confidence",
                 has_contacts, len(new_records), high_conf)

    print_stats(final)


if __name__ == "__main__":
    main()
