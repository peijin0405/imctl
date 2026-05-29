"""
authority_list_scraper.py
Step 5: Scrape authoritative VC/PE directory pages to extract firm names,
then enrich each with Exa search (website, description, contacts, portfolio).
"""

import argparse
import concurrent.futures
import hashlib
import json
import logging
import os
import re
import time
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from exa_py import Exa

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

EXA_API_KEY = os.getenv("EXA_API_KEY")
WEB_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "web", "investors.json")
PROGRESS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "authority_progress.json")

EXA_TIMEOUT = 45  # seconds hard timeout per API call

# ── Authority list URLs ───────────────────────────────────────────────────────

AUTHORITY_LISTS: dict[str, list[str]] = {
    "ai_apps": [
        "https://builtin.com/venture-capital/ai-venture-capital-firms",
        "https://nvca.org/members/",
        "https://a16z.com/portfolio/",
        "https://www.sequoiacap.com/our-companies/",
        "https://firstround.com/companies/",
        "https://www.bvp.com/portfolio",
        "https://www.generalcatalyst.com/portfolio",
        "https://lsvp.com/portfolio/",
        "https://www.forbes.com/lists/midas/",
        "https://www.cbinsights.com/research/artificial-intelligence-top-investors/",
    ],
    "ai_hardware": [
        "https://builtin.com/venture-capital/robotics-venture-capital-firms",
        "https://www.therobotreport.com/robotics-venture-capital-firms/",
        "https://playground.global/portfolio",
        "https://eclipse.vc/portfolio",
        "https://luxcapital.com/companies",
        "https://www.cbinsights.com/research/robotics-venture-capital-funding/",
        "https://nvca.org/members/",
    ],
    "semiconductor": [
        "https://builtin.com/venture-capital/semiconductor-venture-capital-firms",
        "https://www.semi.org/en/communities/member-directory",
        "https://www.semiconductors.org/members/",
        "https://www.intelcapital.com/portfolio/",
        "https://www.qualcommventures.com/portfolio/",
        "https://www.cbinsights.com/research/semiconductor-chip-venture-capital/",
    ],
    "healthcare": [
        "https://builtin.com/venture-capital/healthcare-venture-capital-firms",
        "https://rockhealth.com/insights/",
        "https://www.gv.com/portfolio/",
        "https://www.orbimed.com/portfolio",
        "https://www.archventure.com/portfolio/",
        "https://www.atlasventure.com/portfolio/",
        "https://www.thirdrockventures.com/portfolio/",
        "https://www.versantventures.com/portfolio/",
        "https://www.fiercehealthcare.com/venture/top-healthcare-venture-capital-firms",
        "https://www.cbinsights.com/research/digital-health-venture-capital/",
    ],
    "edtech": [
        "https://builtin.com/venture-capital/edtech-venture-capital-firms",
        "https://www.edsurge.com/research/guides/investors",
        "https://www.gsvventures.com/portfolio",
        "https://www.owlventures.com/portfolio",
        "https://reachcapital.com/portfolio/",
        "https://www.cbinsights.com/research/edtech-venture-capital/",
    ],
    "fintech": [
        "https://builtin.com/venture-capital/fintech-venture-capital-firms",
        "https://ribbitcap.com/portfolio/",
        "https://nyca.com/portfolio/",
        "https://qedinvestors.com/portfolio/",
        "https://www.anthemis.com/portfolio/",
        "https://www.paypalventures.com/portfolio",
        "https://www.cbinsights.com/research/fintech-venture-capital-investors/",
        "https://www.fintechfutures.com/venture-capital/",
    ],
    "greentech": [
        "https://builtin.com/venture-capital/clean-tech-venture-capital-firms",
        "https://www.ctvc.co/investors/",
        "https://congruentvc.com/portfolio/",
        "https://www.preludeventures.com/portfolio/",
        "https://www.closedlooppartners.com/portfolio/",
        "https://www.cbinsights.com/research/climate-tech-venture-capital/",
    ],
    "energy": [
        "https://builtin.com/venture-capital/clean-energy-venture-capital",
        "https://www.energyimpactpartners.com/portfolio/",
        "https://cleanenergyventures.com/portfolio/",
        "https://lowercarboncapital.com/companies/",
        "https://galvanizeclimate.org/portfolio/",
        "https://www.breakthroughenergy.org/our-work/breakthrough-energy-ventures",
        "https://www.cbinsights.com/research/climate-tech-venture-capital/",
    ],
}

# ── Domain exclusions for Exa ─────────────────────────────────────────────────

AGGREGATOR_DOMAINS = [
    "crunchbase.com", "pitchbook.com", "linkedin.com", "wikipedia.org",
    "twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com",
    "bloomberg.com", "techcrunch.com", "forbes.com", "reuters.com",
    "wsj.com", "ft.com", "axios.com", "fortune.com", "businesswire.com",
    "prnewswire.com", "globenewswire.com", "sec.gov", "edgar.sec.gov",
    "cbinsights.com", "venturebeat.com", "builtin.com",
]

# ── Vocabulary ─────────────────────────────────────────────────────────────────

FIRM_SUFFIXES = [
    "ventures", "venture", "capital", "partners", "fund", "funds",
    "investments", "equity", "management", "advisors", "advisers",
    "holdings", "group", "associates", "asset", "growth", "health",
    "bio", "tech", "energy", "innovation", "innovations", "labs",
]

EXCLUDE_LINK_TEXT = frozenset([
    "click here", "read more", "learn more", "sign up", "contact us",
    "follow us", "subscribe", "newsletter", "terms of", "privacy policy",
    "all rights", "copyright", "view all", "see all", "show more",
    "load more", "back to top", "home", "about", "portfolio", "team",
    "news", "blog", "careers", "services", "resources", "insights",
    "press", "events", "our work", "our companies", "companies",
])

US_GEO_TERMS = [
    "united states", " usa", " u.s.", "new york", "san francisco",
    "silicon valley", "boston", "los angeles", "chicago", "austin",
    "seattle", "denver", "miami", "atlanta", "washington",
    "menlo park", "palo alto", "cambridge", "new haven",
    "san jose", "san diego", "minneapolis", "nashville", "phoenix",
    "princeton", "philadelphia", "dallas", "houston", "detroit",
    "baltimore", "portland", "salt lake", "new haven", "raleigh",
    " ny ", " ca ", " tx ", " ma ", " wa ", " il ",
]

FOREIGN_SIGNALS = [
    "london", "berlin", "paris", "beijing", "shanghai", "singapore",
    "tokyo", "mumbai", "toronto", "sydney", ".co.uk", ".de", ".fr",
    ".cn", ".jp", ".au", ".ca", ".sg",
]

SECTOR_KEYWORDS: dict[str, list[str]] = {
    "ai_apps": [
        "artificial intelligence", "machine learning", "deep learning",
        "generative ai", "llm", "large language model", "enterprise ai",
        "ai saas", "foundation model", "nlp", "natural language",
        "mlops", "ai infrastructure", "data science", "computer vision",
    ],
    "ai_hardware": [
        "robotics", "robot", "autonomous vehicle", "self-driving",
        "industrial automation", "embodied ai", "edge ai", "humanoid",
        "drone", "lidar", "autonomous systems", "mechatronics",
    ],
    "semiconductor": [
        "semiconductor", "chip", "fabless", "eda", "fpga", "gpu",
        "sic", "gan", "chiplet", "integrated circuit", "wafer",
        "foundry", "photonics", "quantum computing", "microchip",
    ],
    "healthcare": [
        "health", "healthcare", "biotech", "biotechnology",
        "pharmaceutical", "pharma", "life sciences", "medical",
        "clinical", "therapeutics", "drug", "genomics", "diagnostics",
        "medtech", "digital health", "biopharma", "bioscience",
        "oncology", "neuroscience",
    ],
    "edtech": [
        "education", "edtech", "e-learning", "elearning", "learning",
        "workforce training", "higher education", "academic",
        "online learning", "skill development",
    ],
    "fintech": [
        "fintech", "financial technology", "payments", "payment",
        "blockchain", "crypto", "cryptocurrency", "insurtech",
        "wealthtech", "neobank", "lending", "regtech", "defi",
        "digital banking",
    ],
    "greentech": [
        "cleantech", "clean tech", "environmental", "sustainability",
        "sustainable", "carbon", "recycling", "circular economy",
        "green chemistry", "esg", "impact investing", "climate tech",
        "net zero",
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
    "Series A": ["series a", "series-a"],
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
    "santa monica": ("CA", "Santa Monica"),
    "san jose": ("CA", "San Jose"),
    "san diego": ("CA", "San Diego"),
    "new york": ("NY", "New York"),
    " nyc ": ("NY", "New York"),
    "manhattan": ("NY", "New York"),
    "brooklyn": ("NY", "Brooklyn"),
    "boston": ("MA", "Boston"),
    "cambridge": ("MA", "Cambridge"),
    "chicago": ("IL", "Chicago"),
    "austin": ("TX", "Austin"),
    "dallas": ("TX", "Dallas"),
    "houston": ("TX", "Houston"),
    "seattle": ("WA", "Seattle"),
    "bellevue": ("WA", "Bellevue"),
    "denver": ("CO", "Denver"),
    "boulder": ("CO", "Boulder"),
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
    "pittsburgh": ("PA", "Pittsburgh"),
    "raleigh": ("NC", "Raleigh"),
    "salt lake city": ("UT", "Salt Lake City"),
    "detroit": ("MI", "Detroit"),
    "baltimore": ("MD", "Baltimore"),
}

# ── Regex patterns ─────────────────────────────────────────────────────────────

_DATE_RE = re.compile(
    r"^\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d",
    re.IGNORECASE,
)
_METRIC_RE = re.compile(r"^\s*[\$€£¥]?\d[\d,\.]+[BMK%]?\s*\+?$", re.IGNORECASE)
_CONNECTIVES_RE = re.compile(r"\b(?:and|or|in|of|for|the|with|at|by|from)\b")
_SENTENCE_STARTERS = frozenset([
    "get", "led", "highly", "strategic", "growth", "looking", "view",
    "visit", "find", "explore", "discover", "learn", "read", "see",
    "founded", "based", "focused", "dedicated", "committed", "we",
    "our", "their", "its", "this", "that", "these", "those",
])
_FOUNDED_RE = re.compile(
    r"\b(?:founded|established|est\.?|since)\s+(?:in\s+)?(\d{4})\b",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(
    r"\b(?:General\s+Partner|Managing\s+(?:Partner|Director)|"
    r"Partner|GP|Co-Founder|Founder|CEO|CTO|President|MD|"
    r"Vice\s+President|VP|Principal|Associate|Director|"
    r"Investment\s+Partner|Operating\s+Partner|Senior\s+Partner|"
    r"Venture\s+Partner)\b",
    re.IGNORECASE,
)

# Contact regexes
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
_LINKEDIN_RE = re.compile(r"linkedin\.com/in/[\w-]+")
_PERSON_NAME_RE = re.compile(
    r"\b([A-Z][a-z]{1,20}(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]{1,20})\s*[,\-–]?\s*"
    r"(General\s+Partner|Managing\s+(?:Partner|Director)|"
    r"Partner|GP|Co-Founder|Founder|CEO|CTO|President|MD|"
    r"Vice\s+President|VP|Principal|Associate|Director|"
    r"Investment\s+Partner|Operating\s+Partner|Senior\s+Partner|"
    r"Venture\s+Partner)"
)

REQUESTS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Exa client ────────────────────────────────────────────────────────────────

_exa_client: Exa | None = None


def _get_exa() -> Exa:
    global _exa_client
    if _exa_client is None:
        _exa_client = Exa(EXA_API_KEY)
    return _exa_client


def _exa_call(fn, timeout: int = EXA_TIMEOUT):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            log.warning("Exa call timed out after %ds", timeout)
            return None


# ── Name validation ───────────────────────────────────────────────────────────

def is_valid_firm_name(text: str) -> bool:
    if not text:
        return False
    if not (3 <= len(text) <= 70):
        return False
    words = text.split()
    if not (1 <= len(words) <= 7):
        return False
    text_lower = text.lower()
    if not any(sfx in text_lower for sfx in FIRM_SUFFIXES):
        return False
    if text_lower in EXCLUDE_LINK_TEXT:
        return False
    if _DATE_RE.match(text):
        return False
    if _METRIC_RE.match(text):
        return False
    first_word = words[0].lower()
    if first_word in _SENTENCE_STARTERS:
        return False
    if not any(w[0].isupper() for w in words if w):
        return False
    # Reject names that are obviously sentences (connective in lowercase position)
    if len(words) >= 3 and _CONNECTIVES_RE.search(" ".join(words[1:])):
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


def is_duplicate(name: str, hq_state: str | None, existing_norm: dict[str, str]) -> bool:
    norm = normalize_name(name)
    if norm in existing_norm:
        return True
    for ex_norm, ex_state in existing_norm.items():
        if SequenceMatcher(None, norm, ex_norm).ratio() > 0.90:
            if not hq_state or not ex_state or hq_state == ex_state:
                return True
    return False


# ── HTML scraping ─────────────────────────────────────────────────────────────

def extract_firm_names(url: str) -> list[str]:
    try:
        resp = requests.get(url, headers=REQUESTS_HEADERS, timeout=15, allow_redirects=True)
        if resp.status_code != 200:
            log.warning("  HTTP %d: %s", resp.status_code, url)
            return []
    except Exception as e:
        log.warning("  Fetch error %s: %s", url, e)
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    firms: set[str] = set()

    # Strategy 1: anchor text
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        if is_valid_firm_name(text):
            firms.add(text)

    # Strategy 2: heading tags
    for tag in soup.find_all(["h2", "h3", "h4"]):
        text = tag.get_text(strip=True)
        if is_valid_firm_name(text):
            firms.add(text)

    # Strategy 3: class-named containers
    list_classes = [
        "portfolio", "investor", "member", "company", "firm",
        "partner", "fund", "card", "item", "entry", "name",
    ]
    for cls in list_classes:
        for tag in soup.find_all(class_=re.compile(cls, re.I)):
            text = tag.get_text(strip=True)
            if is_valid_firm_name(text) and len(text) < 60:
                firms.add(text)

    # Strategy 4: line-by-line text
    for line in soup.get_text(separator="\n").split("\n"):
        line = line.strip()
        if is_valid_firm_name(line):
            firms.add(line)

    result = sorted(firms)
    log.info("  ✅ %s → %d candidate names", url, len(result))
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
    text_lower = text.lower()
    for city, (state, city_name) in CITY_STATE_MAP.items():
        if city in text_lower:
            return state, city_name
    return None, None


def extract_stages(text: str) -> list[str]:
    text_lower = text.lower()
    found = [s for s, kws in STAGE_KEYWORDS.items()
             if any(kw in text_lower for kw in kws)]
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


def extract_portfolio_from_text(text: str) -> list[str]:
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


def extract_people_from_text(text: str) -> list[dict]:
    people: list[dict] = []
    seen: set[str] = set()
    for name, title in _PERSON_NAME_RE.findall(text):
        name = re.sub(r"\s+", " ", name).strip()
        key = name.lower()
        if key not in seen and 2 <= len(name.split()) <= 4:
            seen.add(key)
            people.append({"name": name, "title": title})
    return people[:8]


def extract_contacts(text: str, highlights: list[str]) -> list[dict]:
    """
    Extract contact info with confidence levels.
    high:   name + title + at least one contact method
    medium: name + title, no contact method
    low:    contact method with no associated name
    """
    full_text = text + "\n" + "\n".join(highlights)

    emails = [m.group(0) for m in _EMAIL_RE.finditer(full_text)]
    phones = [m.group(0) for m in _PHONE_RE.finditer(full_text)]
    linkedins = [
        "https://" + m.group(0) if not m.group(0).startswith("http") else m.group(0)
        for m in _LINKEDIN_RE.finditer(full_text)
    ]

    # Collect named people with positions in text
    named: list[dict] = []
    for m in _PERSON_NAME_RE.finditer(full_text):
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        title = m.group(2)
        if 2 <= len(name.split()) <= 4:
            named.append({"name": name, "title": title, "pos": m.start()})

    contacts: list[dict] = []
    seen_names: set[str] = set()
    PROXIMITY = 200  # chars

    for person in named:
        if person["name"].lower() in seen_names:
            continue
        seen_names.add(person["name"].lower())
        pos = person["pos"]

        # Find contact methods within PROXIMITY chars of this name
        nearby_email = next(
            (e for e in emails
             if abs(full_text.find(e) - pos) <= PROXIMITY), None
        )
        nearby_phone = next(
            (p for p in phones
             if abs(full_text.find(p) - pos) <= PROXIMITY), None
        )
        nearby_linkedin = next(
            (li for li in linkedins
             if abs(full_text.find(li.replace("https://", "")) - pos) <= PROXIMITY), None
        )

        has_contact = any([nearby_email, nearby_phone, nearby_linkedin])
        confidence = "high" if has_contact else "medium"

        contacts.append({
            "name": person["name"],
            "title": person["title"],
            "email": nearby_email,
            "phone": nearby_phone,
            "linkedin": nearby_linkedin,
            "confidence": confidence,
        })

    # Orphan contact methods (not near any named person)
    used_emails = {c["email"] for c in contacts if c.get("email")}
    used_phones = {c["phone"] for c in contacts if c.get("phone")}
    used_linkedins = {c["linkedin"] for c in contacts if c.get("linkedin")}

    for email in emails:
        if email not in used_emails:
            contacts.append({
                "name": None, "title": None,
                "email": email, "phone": None, "linkedin": None,
                "confidence": "low",
            })
    for phone in phones:
        if phone not in used_phones:
            contacts.append({
                "name": None, "title": None,
                "email": None, "phone": phone, "linkedin": None,
                "confidence": "low",
            })
    for li in linkedins:
        if li not in used_linkedins:
            contacts.append({
                "name": None, "title": None,
                "email": None, "phone": None, "linkedin": li,
                "confidence": "low",
            })

    return contacts[:20]


def infer_investor_type(name: str, text: str) -> str:
    combined = (name + " " + text).lower()
    if any(k in combined for k in ["venture capital", "venture fund", "vc fund"]):
        return "VC"
    if any(k in combined for k in ["private equity", "buyout", "growth equity"]):
        return "PE"
    if any(k in combined for k in ["hedge fund", "long/short"]):
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

    # US verification
    if not any(geo in full_lower for geo in US_GEO_TERMS):
        if any(f in url.lower() for f in FOREIGN_SIGNALS):
            return None

    # Must mention investing
    invest_terms = {"invest", "portfolio", "fund", "capital", "venture",
                    "equity", "back", "stage", "startup", "financed"}
    if not any(t in full_lower for t in invest_terms):
        return None

    sector_primary, sectors_all, focus = extract_sectors(full_text)
    if sector_primary == "general":
        sector_primary = sector
        sectors_all = [sector]

    hq_state, hq_city = extract_location(full_text)
    portfolio = extract_portfolio_from_text(full_text)
    key_people = extract_people_from_text(full_text)
    contacts = extract_contacts(text_body, highlights)
    stages = extract_stages(full_text)
    investor_type = infer_investor_type(firm_name, full_text)
    description = extract_description(full_text)
    founded_year = extract_founded_year(full_text)

    # Website: use Exa URL root, skip aggregators
    website = None
    if url and not any(agg in url for agg in AGGREGATOR_DOMAINS):
        parsed = urlparse(url)
        website = f"{parsed.scheme}://{parsed.netloc}"

    firm_id = "auth_" + hashlib.md5(
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
        "data_source": "authority_list",
        "data_confidence": "high",
        "scraped_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def print_stats(data: list[dict]) -> None:
    exa_srcs = {"exa_directory", "exa_direct", "authority_list"}
    by_src = Counter(d.get("data_source") for d in data)
    by_sector = Counter(d.get("sector") for d in data)
    print(f"\nTotal records: {len(data)}")
    print("By source:")
    for s, c in by_src.most_common():
        print(f"  {s}: {c}")
    print("By sector (top 12):")
    for s, c in by_sector.most_common(12):
        print(f"  {s}: {c}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Authority list scraper + Exa enrichment")
    parser.add_argument("--sectors", nargs="+", metavar="SECTOR",
                        help="Only process these sectors")
    parser.add_argument("--resume", action="store_true",
                        help="Skip already-completed URLs")
    parser.add_argument("--test", action="store_true",
                        help="Test mode: one URL per sector, no Exa calls")
    parser.add_argument("--stats", action="store_true",
                        help="Print dataset stats and exit")
    args = parser.parse_args()

    with open(WEB_DATA_PATH, encoding="utf-8") as f:
        existing = json.load(f)
    log.info("Loaded %d existing records", len(existing))

    if args.stats:
        print_stats(existing)
        return

    if not EXA_API_KEY:
        log.error("EXA_API_KEY not set")
        return

    # Build dedup index
    existing_norm: dict[str, str] = {
        normalize_name(inv.get("name", "")): (inv.get("hq_state") or "")
        for inv in existing
    }

    # Load checkpoint
    completed_urls: set[str] = set()
    if args.resume and os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            prog = json.load(f)
            completed_urls = set(prog.get("completed_urls", []))
        log.info("Resume: %d URLs already done", len(completed_urls))

    sectors_to_run = args.sectors or list(AUTHORITY_LISTS.keys())
    new_records: list[dict] = []
    sector_stats: Counter = Counter()

    for sector in sectors_to_run:
        urls = AUTHORITY_LISTS.get(sector, [])
        if args.test:
            urls = urls[:1]

        log.info("")
        log.info("=" * 55)
        log.info("Sector: %s — %d URLs", sector, len(urls))
        log.info("=" * 55)

        sector_firms: set[str] = set()

        for url in urls:
            if url in completed_urls:
                log.info("  ⏭  Skip (done): %s", url)
                continue

            log.info("  📋 Scraping: %s", url)
            firm_names = extract_firm_names(url)
            time.sleep(1.5)

            for firm_name in firm_names:
                norm_fn = firm_name.lower()
                if norm_fn in sector_firms:
                    continue
                sector_firms.add(norm_fn)

                if is_duplicate(firm_name, None, existing_norm):
                    continue

                if args.test:
                    log.info("    [TEST] would Exa-enrich: %s", firm_name)
                    continue

                log.info("    🔍 Exa: %s", firm_name)
                record = enrich_with_exa(firm_name, sector)
                time.sleep(0.5)

                if record:
                    new_records.append(record)
                    sector_stats[sector] += 1
                    existing_norm[normalize_name(firm_name)] = record.get("hq_state") or ""

            # Checkpoint
            completed_urls.add(url)
            with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
                json.dump({"completed_urls": list(completed_urls)}, f)

            # Write-through
            merged = existing + new_records
            with open(WEB_DATA_PATH, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)

            log.info("  💾 New so far: %d total, %d this sector",
                     len(new_records), sector_stats[sector])

    final = existing + new_records
    log.info("")
    log.info("=" * 55)
    log.info("Done. +%d new records → %d total", len(new_records), len(final))
    log.info("=" * 55)
    log.info("New by sector:")
    for s, c in sector_stats.most_common():
        log.info("  %-15s +%d", s, c)
    print_stats(final)


if __name__ == "__main__":
    main()
