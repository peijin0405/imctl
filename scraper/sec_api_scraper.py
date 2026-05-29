"""
SEC API Scraper — pulls US investment firm data from sec-api.io
Layer 1: Form ADV /firm endpoint (main, 10,000+ records)
Layer 2: Form D /form-d endpoint (early-stage supplement)
Run: python scraper/sec_api_scraper.py [--layer 1|2] [--resume] [--test]
"""

import argparse
import json
import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
WEB_INVESTORS = ROOT / "web" / "investors.json"
DATA_DIR = ROOT / "data"
RAW_DIR = ROOT / "data" / "raw"
PROGRESS_FILE = ROOT / "data" / "sec_progress.json"

TODAY = datetime.now().strftime("%Y%m%d")
RAW_JSONL = RAW_DIR / f"sec_raw_{TODAY}.jsonl"
ERROR_LOG = RAW_DIR / f"errors_{TODAY}.log"
BACKUP_JSON = DATA_DIR / f"sec_investors_{TODAY}.json"

RAW_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── API Config ─────────────────────────────────────────────────────────────────
SEC_API_KEY = os.getenv("SEC_API_KEY")
if not SEC_API_KEY:
    sys.exit("ERROR: SEC_API_KEY not set in .env")

# Token goes in URL, not header (per sec-api SDK convention)
ADV_FIRM_URL = f"https://api.sec-api.io/form-adv/firm?token={SEC_API_KEY}"
FORM_D_URL = f"https://api.sec-api.io/form-d?token={SEC_API_KEY}"

PAGE_SIZE = 50
MAX_PAGES_PER_QUERY = 200          # 200 × 50 = 10,000 records max per query
REQUEST_DELAY = 1.2                # seconds between requests (~0.8 req/s, safe for 1 req/s limit)
RETRY_DELAYS = [60, 120, 240]      # after rate-limit: wait 1min, 2min, 4min
RATE_LIMIT_COOLDOWN = 300          # sleep 5min after exhausting retries before next state


def _make_session() -> requests.Session:
    """Return a session that bypasses local proxy (Clash/Charles/etc.)."""
    s = requests.Session()
    s.trust_env = False            # ignore all *_proxy env vars
    s.headers.update({"Content-Type": "application/json"})
    return s


SESSION = _make_session()

# ── US State set ───────────────────────────────────────────────────────────────
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}

# ── Sector keywords ────────────────────────────────────────────────────────────
SECTOR_KEYWORDS = {
    "ai_apps": [
        "artificial intelligence", "machine learning", "large language model",
        "generative ai", "llm", "enterprise ai", "ai saas", "foundation model",
        "natural language processing", "mlops", "ai infrastructure", "deep learning",
        "neural network", "intelligent automation", "data science", "predictive analytics",
    ],
    "ai_hardware": [
        "robotics", "autonomous vehicle", "self-driving", "industrial automation",
        "embodied ai", "edge ai", "humanoid", "drone", "lidar", "computer vision",
        "autonomous driving", "warehouse automation", "unmanned systems",
    ],
    "semiconductor": [
        "semiconductor", "chip", "fabless", "eda", "fpga", "gpu", "sic", "gan",
        "advanced packaging", "chiplet", "integrated circuit", "wafer", "foundry",
        "asic", "photonics", "rf semiconductor", "power semiconductor",
    ],
    "healthcare": [
        "healthcare", "health care", "digital health", "biotech", "pharmaceutical",
        "medtech", "life sciences", "clinical", "therapeutics", "drug discovery",
        "medical device", "telemedicine", "genomics", "diagnostics", "biopharma",
        "health technology", "oncology", "precision medicine", "bioinformatics",
    ],
    "edtech": [
        "education technology", "edtech", "e-learning", "learning platform",
        "workforce training", "higher education", "k-12", "online learning",
        "learning management", "skills training", "corporate learning",
    ],
    "fintech": [
        "fintech", "financial technology", "payments", "blockchain", "cryptocurrency",
        "quantitative", "algorithmic trading", "insurtech", "wealthtech", "neobank",
        "lending", "regtech", "defi", "digital banking", "embedded finance",
        "crypto", "web3", "decentralized finance",
    ],
    "greentech": [
        "cleantech", "environmental", "sustainability", "carbon capture", "recycling",
        "circular economy", "green chemistry", "bioplastics", "water technology",
        "waste management", "eco-friendly", "sustainable agriculture", "carbon neutral",
    ],
    "energy": [
        "clean energy", "renewable energy", "solar", "wind energy", "battery storage",
        "energy storage", "hydrogen", "nuclear", "electric vehicle", "decarbonization",
        "energy transition", "grid", "offshore wind", "geothermal", "fuel cell",
    ],
}

STAGE_KEYWORDS = {
    "Pre-Seed": ["pre-seed", "pre seed", "idea stage", "inception stage"],
    "Seed": ["seed stage", "seed fund", "seed-stage", "early stage", "seed capital"],
    "Series A": ["series a", "series-a"],
    "Series B": ["series b", "growth stage"],
    "Series C+": ["series c", "late stage", "pre-ipo", "series d", "series e"],
    "Growth": ["growth equity", "expansion stage", "growth capital", "growth-stage"],
    "Buyout": ["buyout", "leveraged buyout", "lbo", "management buyout"],
    "Multi-Stage": ["multi-stage", "all stages", "across stages", "various stages"],
}

# ── Layer 1 ADV queries ────────────────────────────────────────────────────────
# PascalCase field paths, values use Y/N (not true/false)
# Q8A1 = advises private fund(s), Q8A2 = client includes pooled investment vehicles
# Q7A16 = clients include pooled investment vehicles
ADV_QUERIES = [
    # Broadest catch: advisers whose clients include pooled investment vehicles (VC/PE/HF)
    ("q8a2_all",    "FormInfo.Part1A.Item8A.Q8A2:Y"),
    # Registered private fund advisers
    ("q8a1_all",    "FormInfo.Part1A.Item8A.Q8A1:Y"),
    # Item7A: clients include pooled investment vehicles
    ("q7a16_all",   "FormInfo.Part1A.Item7A.Q7A16:Y"),
    # Catch hedge-fund-focused advisers (Q8A10 field seen in docs)
    ("q8a10_all",   "FormInfo.Part1A.Item8A.Q8A10:Y"),
    # Real-assets / infrastructure PE
    ("q8a3_all",    "FormInfo.Part1A.Item8A.Q8A3:Y"),
]

# For any query hitting the 10,000 cap we re-run split by state
MAJOR_STATES = [
    "CA", "NY", "TX", "FL", "MA", "IL", "WA", "CO", "CT", "NJ",
    "GA", "NC", "VA", "PA", "OH", "MD", "AZ", "MN", "OR", "UT",
    "TN", "NV", "MI", "IN", "WI", "MO", "SC", "NH", "DC", "AL",
    "AK", "AR", "DE", "HI", "ID", "IA", "KS", "KY", "LA", "ME",
    "MS", "MT", "NE", "NM", "ND", "OK", "RI", "SD", "VT", "WV", "WY",
]

# ── Sector inference ───────────────────────────────────────────────────────────

def infer_sectors(text: str) -> tuple[str, list[str]]:
    tl = text.lower()
    scores: dict[str, int] = {}
    for sector, kws in SECTOR_KEYWORDS.items():
        hits = sum(1 for kw in kws if kw in tl)
        if hits:
            scores[sector] = hits
    if not scores:
        return "general", []
    primary = max(scores, key=scores.get)
    return primary, list(scores.keys())


def infer_investor_type(item8a: dict, item7a: dict, name: str) -> str:
    def yes(d: dict, *keys) -> bool:
        return any(d.get(k) == "Y" for k in keys)

    # Item8A checks (private fund types)
    if yes(item8a, "Q8A16", "Q8A2"):   # Q8A2 = pooled vehicles (broad VC/PE)
        name_l = name.lower()
        if any(k in name_l for k in ["venture", " vc ", "ventures", "growth"]):
            return "VC"
        if any(k in name_l for k in ["equity", "buyout", "capital partners"]):
            return "PE"
        # default to VC for Q8A2 advisers
        return "VC"
    if yes(item8a, "Q8A10"):
        return "Hedge Fund"
    if yes(item8a, "Q8A9"):
        return "Fund of Funds"

    # Name heuristics
    nl = name.lower()
    if any(k in nl for k in ["venture", " vc ", "ventures"]):
        return "VC"
    if any(k in nl for k in ["private equity", "buyout", "leveraged"]):
        return "PE"
    if any(k in nl for k in ["hedge", "arbitrage", "macro"]):
        return "Hedge Fund"
    if any(k in nl for k in ["family office", "family investment", "family wealth"]):
        return "Family Office"
    return "Investment Adviser"


def infer_stages(text: str) -> list[str]:
    tl = text.lower()
    matched = [s for s, kws in STAGE_KEYWORDS.items() if any(kw in tl for kw in kws)]
    return matched if matched else ["Unknown"]


def normalize_website(raw) -> str | None:
    if not raw:
        return None
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if not raw:
        return None
    raw = str(raw).strip()
    if not raw or raw.lower() in ("n/a", "none", "na"):
        return None
    if not raw.startswith("http"):
        raw = "https://" + raw
    return raw


def safe_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(str(val).replace(",", "").replace("$", "").strip())
    except Exception:
        return None


def is_us_firm(addr: dict) -> bool:
    country = (addr.get("Cntry") or "").strip().lower()
    state = (addr.get("State") or "").strip().upper()
    return (country in ("united states", "us", "usa") or country == "") and state in US_STATES


# ── Raw record → output schema ─────────────────────────────────────────────────

def adv_to_record(firm: dict) -> dict | None:
    info = firm.get("Info", {}) or {}
    addr = firm.get("MainAddr", {}) or {}

    if not is_us_firm(addr):
        return None

    name = (info.get("BusNm") or info.get("LegalNm") or "").strip()
    if not name:
        return None

    p1a = (firm.get("FormInfo", {}) or {}).get("Part1A", {}) or {}

    item8a = p1a.get("Item8A", {}) or {}
    item7a = p1a.get("Item7A", {}) or {}
    item5f = p1a.get("Item5F", {}) or {}

    # AUM — Item5F has various sub-fields, try common ones
    aum = None
    for k in ("TtlRgltrAst", "TotalRegulatoryAssets", "RgltrAst", "AUM",
               "TtlAst", "TtlBillableAst"):
        v = item5f.get(k)
        if v:
            aum = safe_int(v)
            if aum:
                break
    # If Item5F is a plain number
    if aum is None and isinstance(item5f, (int, float)):
        aum = safe_int(item5f)

    # Filter: skip tiny personal advisers (no fund type flags and tiny/no AUM)
    has_fund_flags = any(item8a.get(k) == "Y" for k in item8a) or \
                     any(item7a.get(k) == "Y" for k in ["Q7A16"])
    if not has_fund_flags and aum is not None and aum < 5_000_000:
        return None

    # Website
    item1 = p1a.get("Item1", {}) or {}
    web_container = item1.get("WebAddrs", {}) or {}
    if isinstance(web_container, dict):
        # Try multi-value list first, then single value
        raw_web = web_container.get("WebAddrs") or web_container.get("WebAddr")
    elif isinstance(web_container, list):
        raw_web = web_container
    else:
        raw_web = None
    website = normalize_website(raw_web)

    # CRD / CIK
    sec_crd = str(info.get("FirmCrdNb") or "").strip() or None
    sec_cik = str(info.get("SECNb") or "").strip() or None
    if sec_cik:
        sec_cik = sec_cik.zfill(10)

    uid = f"sec_{sec_cik or sec_crd or name[:25].replace(' ', '_').replace(',', '')}"
    sec_url = (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={sec_cik}"
               if sec_cik else None)

    # Portfolio from ScheduleD Item7B private fund list
    sched_d = (firm.get("scheduleD") or firm.get("ScheduleD") or {}) or {}
    item7b = sched_d.get("item7B") or sched_d.get("Item7B") or []
    portfolio: list[str] = []
    for fund in (item7b if isinstance(item7b, list) else [])[:10]:
        fn = fund.get("FundName") or fund.get("PrivFundNm") or ""
        if fn:
            portfolio.append(fn)

    # Key people
    key_people: list[dict] = []
    contact = item1.get("ContctNm") or item1.get("Q1F1Nm")
    if contact:
        key_people.append({"name": contact, "title": "Contact"})

    # Sector — blend name + portfolio fund names + any description text
    text_blob = " ".join(filter(None, [name] + portfolio))
    primary_sector, all_sectors = infer_sectors(text_blob)

    state = (addr.get("State") or "").strip().upper()
    city = (addr.get("City") or "").strip().title()
    investor_type = infer_investor_type(item8a, item7a, name)
    stages = infer_stages(text_blob + " " + investor_type)

    return {
        "id": uid,
        "name": name,
        "description": None,
        "investor_type": investor_type,
        "sector": primary_sector,
        "sectors": all_sectors,
        "stage": stages,
        "focus": [],
        "region": "North America",
        "hq_state": state,
        "hq_city": city,
        "hq_country": "US",
        "us_verified": True,
        "website": website,
        "aum_usd": aum,
        "fund_size_usd": None,
        "check_size_min_usd": None,
        "check_size_max_usd": None,
        "key_people": key_people,
        "portfolio": portfolio,
        "founded_year": None,
        "sec_cik": sec_cik,
        "sec_crd": sec_crd,
        "sec_form_type": "ADV",
        "sec_filing_url": sec_url,
        "data_source": "sec_api",
        "data_confidence": "high",
        "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def form_d_to_record(doc: dict) -> dict | None:
    issuer = doc.get("issuerInfo", {}) or {}
    name = (issuer.get("issuerName") or "").strip()
    if not name:
        return None

    addr = issuer.get("issuerAddress", {}) or {}
    state = (addr.get("stateOrCountry") or "").strip().upper()
    city = (addr.get("city") or "").strip().title()

    if state not in US_STATES:
        return None

    offering = doc.get("offeringData", {}) or {}
    amount = offering.get("totalOfferingAmount")
    aum = safe_int(amount)

    cik_raw = doc.get("cik") or doc.get("CIK") or ""
    sec_cik = str(cik_raw).strip().zfill(10) if cik_raw else None
    uid = f"sec_d_{sec_cik or name[:25].replace(' ', '_').replace(',', '')}"
    sec_url = (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={sec_cik}"
               if sec_cik else None)

    text_blob = name
    primary_sector, all_sectors = infer_sectors(text_blob)
    stages = infer_stages(text_blob)
    investor_type = infer_investor_type({}, {}, name)

    return {
        "id": uid,
        "name": name,
        "description": None,
        "investor_type": investor_type,
        "sector": primary_sector,
        "sectors": all_sectors,
        "stage": stages,
        "focus": [],
        "region": "North America",
        "hq_state": state,
        "hq_city": city,
        "hq_country": "US",
        "us_verified": True,
        "website": None,
        "aum_usd": aum,
        "fund_size_usd": aum,
        "check_size_min_usd": None,
        "check_size_max_usd": None,
        "key_people": [],
        "portfolio": [],
        "founded_year": None,
        "sec_cik": sec_cik,
        "sec_crd": None,
        "sec_form_type": "D",
        "sec_filing_url": sec_url,
        "data_source": "sec_api",
        "data_confidence": "medium",
        "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ── HTTP helper ────────────────────────────────────────────────────────────────

def api_post(url: str, payload: dict) -> dict | None:
    """POST with retry; returns parsed JSON, None on persistent failure, or 'rate_limited' sentinel."""
    last_was_rate_limit = False
    for attempt in range(len(RETRY_DELAYS) + 1):
        if attempt > 0:
            delay = RETRY_DELAYS[attempt - 1]
            log.warning(f"    retry {attempt}/{len(RETRY_DELAYS)} after {delay}s ...")
            time.sleep(delay)
        try:
            r = SESSION.post(url, json=payload, timeout=30)
            if r.status_code == 402:
                log.error("HTTP 402 — API quota exceeded. Stopping.")
                sys.exit(1)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)-1)]))
                log.warning(f"    rate limited — sleeping {wait}s")
                time.sleep(wait)
                last_was_rate_limit = True
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            log.warning(f"    request error: {e}")
    with open(ERROR_LOG, "a") as f:
        f.write(f"{datetime.now()} ERROR {url} {json.dumps(payload)[:120]}\n")
    # Signal to caller whether this was a rate-limit exhaustion
    return "__rate_limited__" if last_was_rate_limit else None


def append_raw(items: list) -> None:
    if not items:
        return
    with open(RAW_JSONL, "a") as f:
        for item in items:
            f.write(json.dumps(item) + "\n")


# ── Progress ───────────────────────────────────────────────────────────────────

def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"adv_done": [], "formd_done": [], "total": 0}


def save_progress(prog: dict) -> None:
    with open(PROGRESS_FILE, "w") as f:
        json.dump(prog, f, indent=2)


# ── ADV pagination (single query + optional state split) ──────────────────────

def _paginate_adv(query: str, records: list, seen: set,
                  test_mode: bool, label: str) -> int:
    """Paginate a single ADV query. Returns number of new records added."""
    added = 0
    for page in range(0, 1 if test_mode else MAX_PAGES_PER_QUERY):
        payload = {"query": query, "from": str(page * PAGE_SIZE), "size": str(PAGE_SIZE)}
        time.sleep(REQUEST_DELAY)
        resp = api_post(ADV_FIRM_URL, payload)

        if resp == "__rate_limited__":
            log.warning(f"  [{label}] rate limit exhausted at page {page} — "
                        f"cooling down {RATE_LIMIT_COOLDOWN}s before continuing")
            time.sleep(RATE_LIMIT_COOLDOWN)
            # try the same page one more time after cooldown
            resp = api_post(ADV_FIRM_URL, payload)
            if resp in (None, "__rate_limited__"):
                log.warning(f"  [{label}] still rate limited after cooldown, stopping this query")
                break

        if resp is None:
            log.warning(f"  [{label}] null response at page {page}, stopping")
            break

        total_val = resp.get("total", {}).get("value", 0)
        firms = resp.get("filings", [])
        if not firms:
            log.info(f"  [{label}] exhausted at page {page} (total={total_val})")
            break

        append_raw(firms)
        batch = 0
        for firm in firms:
            rec = adv_to_record(firm)
            if rec is None:
                continue
            uid = rec["id"]
            if uid in seen:
                continue
            seen.add(uid)
            records.append(rec)
            batch += 1
            added += 1

        if page % 10 == 0 or batch > 0:
            log.info(f"  [{label}] p{page:>3} +{batch:>3} new | total={len(records):,}")

    return added


def run_layer1(records: list, seen: set, progress: dict, test_mode: bool) -> None:
    log.info("=== LAYER 1: Form ADV bulk fetch ===")

    for key, base_query in ADV_QUERIES:
        if key in progress["adv_done"]:
            log.info(f"  [{key}] already done, skipping")
            continue

        log.info(f"  [{key}] query: {base_query}")

        # Quick probe to check total
        probe = api_post(ADV_FIRM_URL, {"query": base_query, "from": "0", "size": "1"})
        if probe is None:
            log.warning(f"  [{key}] probe failed, skipping")
            continue
        total_val = probe.get("total", {}).get("value", 0)
        relation = probe.get("total", {}).get("relation", "eq")
        log.info(f"  [{key}] total={total_val} relation={relation}")

        if total_val == 0:
            log.info(f"  [{key}] no results, skipping")
            progress["adv_done"].append(key)
            save_progress(progress)
            continue

        # If total hits the 10k cap, split by state to capture everything
        if relation == "gte" and total_val >= 10000 and not test_mode:
            log.info(f"  [{key}] >10k records — splitting by state")
            for state in MAJOR_STATES:
                state_key = f"{key}_{state}"
                if state_key in progress["adv_done"]:
                    continue
                state_query = f"{base_query} AND MainAddr.State:{state}"
                added = _paginate_adv(state_query, records, seen, test_mode, state_key)
                log.info(f"    {state}: +{added} | running total {len(records):,}")
                progress["adv_done"].append(state_key)
                progress["total"] = len(records)
                save_progress(progress)
        else:
            _paginate_adv(base_query, records, seen, test_mode, key)

        progress["adv_done"].append(key)
        progress["total"] = len(records)
        save_progress(progress)
        log.info(f"  [{key}] complete. Running total: {len(records):,}")


# ── Layer 2: Form D supplement ────────────────────────────────────────────────

FORM_D_QUERIES = [
    ("CA", "issuerInfo.issuerAddress.stateOrCountry:CA AND offeringData.typesOfSecuritiesOffered.isEquityType:true"),
    ("NY", "issuerInfo.issuerAddress.stateOrCountry:NY AND offeringData.typesOfSecuritiesOffered.isEquityType:true"),
    ("MA", "issuerInfo.issuerAddress.stateOrCountry:MA AND offeringData.typesOfSecuritiesOffered.isEquityType:true"),
    ("TX", "issuerInfo.issuerAddress.stateOrCountry:TX AND offeringData.typesOfSecuritiesOffered.isEquityType:true"),
    ("WA", "issuerInfo.issuerAddress.stateOrCountry:WA AND offeringData.typesOfSecuritiesOffered.isEquityType:true"),
    ("CO", "issuerInfo.issuerAddress.stateOrCountry:CO AND offeringData.typesOfSecuritiesOffered.isEquityType:true"),
    ("IL", "issuerInfo.issuerAddress.stateOrCountry:IL AND offeringData.typesOfSecuritiesOffered.isEquityType:true"),
]


def run_layer2(records: list, seen: set, progress: dict, test_mode: bool) -> None:
    log.info("=== LAYER 2: Form D supplement ===")
    max_pages = 1 if test_mode else 60  # up to 3,000 per state

    for state_code, query in FORM_D_QUERIES:
        key = f"formd_{state_code}"
        if key in progress["formd_done"]:
            log.info(f"  [{key}] already done, skipping")
            continue

        added = 0
        for page in range(0, max_pages):
            payload = {"query": query, "from": str(page * PAGE_SIZE), "size": str(PAGE_SIZE)}
            time.sleep(REQUEST_DELAY)
            resp = api_post(FORM_D_URL, payload)
            if resp in (None, "__rate_limited__"):
                if resp == "__rate_limited__":
                    log.warning(f"  [{state_code}] rate limited — cooling down {RATE_LIMIT_COOLDOWN}s")
                    time.sleep(RATE_LIMIT_COOLDOWN)
                break

            docs = resp.get("filings", resp.get("offerings", []))
            if not docs:
                break

            append_raw(docs)
            for doc in docs:
                rec = form_d_to_record(doc)
                if rec is None:
                    continue
                uid = rec["id"]
                if uid in seen:
                    continue
                seen.add(uid)
                records.append(rec)
                added += 1

            if page % 10 == 0:
                log.info(f"  [{state_code}] p{page:>3} | running total {len(records):,}")

        progress["formd_done"].append(key)
        progress["total"] = len(records)
        save_progress(progress)
        log.info(f"  [{state_code}] +{added} | total {len(records):,}")


# ── Output & stats ─────────────────────────────────────────────────────────────

def write_output(records: list) -> None:
    log.info(f"Writing {len(records):,} records to {WEB_INVESTORS} ...")
    with open(WEB_INVESTORS, "w") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    with open(BACKUP_JSON, "w") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    log.info(f"Backup: {BACKUP_JSON}")


def print_stats(records: list) -> None:
    total = len(records)
    if not total:
        print("No records collected.")
        return
    sector_c = Counter(r["sector"] for r in records)
    type_c = Counter(r["investor_type"] for r in records)
    has_aum = sum(1 for r in records if r.get("aum_usd"))
    has_web = sum(1 for r in records if r.get("website"))

    print("\n" + "=" * 60)
    print("SEC API Scrape Complete")
    print("=" * 60)
    print(f"Total records:       {total:>10,}")
    print(f"Has AUM:             {has_aum:>10,}  ({has_aum*100//total}%)")
    print(f"Has website:         {has_web:>10,}  ({has_web*100//total}%)")
    print("\nSector distribution:")
    for s, c in sector_c.most_common():
        print(f"  {s:<22} {c:,}")
    print("\nInvestor type:")
    for t, c in type_c.most_common():
        print(f"  {t:<25} {c:,}")
    print(f"\nOutput: {WEB_INVESTORS}")
    print(f"Backup: {BACKUP_JSON}")
    print("=" * 60)


def run_validation(records: list) -> None:
    total = len(records)
    print(f"\n--- Validation ({total:,} records) ---")
    non_us = [r for r in records if r.get("hq_country") != "US" or not r.get("us_verified")]
    print(f"Non-US records:     {len(non_us)}  {'OK' if not non_us else 'WARNING'}")
    print(f"Target >=10,000:    {'OK' if total >= 10000 else f'WARNING only {total}'}")

    sector_c = Counter(r["sector"] for r in records)
    targets = ["ai_apps", "ai_hardware", "semiconductor", "healthcare",
               "edtech", "fintech", "greentech", "energy"]
    all_ok = True
    for s in targets:
        c = sector_c.get(s, 0)
        ok = c >= 100
        if not ok:
            all_ok = False
        print(f"  {s:<22} {c:>6}  {'OK' if ok else 'LOW'}")
    print("All checks passed!" if (not non_us and total >= 10000 and all_ok) else
          "Some targets unmet — see above.")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--layer", type=int, choices=[1, 2])
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--test", action="store_true", help="1 page per query")
    args = parser.parse_args()

    records: list[dict] = []
    seen: set[str] = set()
    progress = load_progress() if args.resume else {"adv_done": [], "formd_done": [], "total": 0}

    if args.test:
        log.info("TEST MODE — 1 page per query")

    try:
        if args.layer is None or args.layer == 1:
            run_layer1(records, seen, progress, args.test)
        if args.layer is None or args.layer == 2:
            run_layer2(records, seen, progress, args.test)
    except KeyboardInterrupt:
        log.warning("Interrupted — saving progress.")
        save_progress(progress)

    if not records:
        log.error("No records collected. Check API key, network, and query syntax.")
        return

    write_output(records)
    print_stats(records)
    run_validation(records)


if __name__ == "__main__":
    main()
