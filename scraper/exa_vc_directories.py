#!/usr/bin/env python3
"""
exa_vc_directories.py — Exa direct VC website scraper

Strategy: For each sector, search directly for VC firm websites using
industry-specific queries. Each Exa result IS the firm's page — no secondary
lookup needed. Extract name from title, sector from query label, description
from highlights. 50 results per query × 10 queries × 8 sectors = up to 4,000
candidates before dedup.

Usage:
  python scraper/exa_vc_directories.py               # full run
  python scraper/exa_vc_directories.py --test        # 2 queries/sector
  python scraper/exa_vc_directories.py --resume      # continue from checkpoint
  python scraper/exa_vc_directories.py --sectors healthcare fintech
  python scraper/exa_vc_directories.py --stats       # show stats only
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

from dotenv import load_dotenv
from exa_py import Exa

sys.path.insert(0, os.path.dirname(__file__))
from exa_search import (
    FOCUS_KEYWORDS,
    SECTOR_KEYWORD_MAP,
    STAGE_RE,
    extract_description,
    extract_firm_name,
    extract_key_people,
    extract_portfolio,
    normalize_stage,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_JSON = os.path.join(ROOT, "web", "investors.json")
PROGRESS_FILE = os.path.join(ROOT, "data", "exa_dir_progress.json")
RAW_LOG = os.path.join(ROOT, "data", "exa_dir_raw.jsonl")

EXA_API_KEY = os.getenv("EXA_API_KEY")
EXA_TIMEOUT = 45  # seconds per API call

EXCLUDE_DOMAINS = [
    "crunchbase.com", "pitchbook.com", "linkedin.com", "wikipedia.org",
    "twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com",
    "bloomberg.com", "techcrunch.com", "forbes.com", "reuters.com",
    "wsj.com", "ft.com", "axios.com", "fortune.com", "businesswire.com",
    "prnewswire.com", "globenewswire.com", "sec.gov", "edgar.sec.gov",
    "cbinsights.com", "venturebeat.com", "crunchbase.com",
]

# ── Queries: 10 per sector, targeting VC firm official websites ────────────────
SECTOR_QUERIES: dict[str, list[str]] = {
    "ai_apps": [
        "artificial intelligence venture capital fund portfolio companies United States",
        "machine learning AI startup investors seed series A fund",
        "generative AI LLM venture fund portfolio investments United States",
        "enterprise AI SaaS venture capital firm investment thesis",
        "foundation model AI startup investor fund United States portfolio",
        "AI infrastructure MLOps vector database startup venture fund",
        "computer vision NLP deep learning venture capital fund portfolio",
        "AI agent autonomous workflow venture capital investments United States",
        "AI developer tools productivity venture capital fund investments",
        "multimodal AI startup venture fund portfolio companies US",
    ],
    "ai_hardware": [
        "robotics venture capital fund portfolio companies United States",
        "autonomous vehicle self-driving startup investor fund portfolio",
        "industrial automation manufacturing robot venture capital United States",
        "humanoid robot embodied AI venture fund investments portfolio",
        "drone UAV autonomous systems venture capital portfolio United States",
        "edge AI inference hardware chip startup investor fund",
        "sensor fusion LIDAR perception startup venture capital investments",
        "physical AI deeptech hardware startup investor fund United States",
        "exoskeleton wearable robotics venture capital fund portfolio",
        "space technology satellite startup venture capital fund United States",
    ],
    "semiconductor": [
        "semiconductor venture capital fund portfolio companies United States",
        "chip design fabless startup investor fund United States portfolio",
        "AI chip GPU neural processor startup investor fund portfolio",
        "power electronics SiC GaN wide bandgap semiconductor venture capital",
        "EDA chip design tools IP core startup investor fund",
        "advanced packaging chiplet heterogeneous integration venture capital",
        "quantum computing venture capital fund portfolio United States",
        "photonics photonic integrated circuit startup venture capital",
        "MEMS sensor semiconductor materials startup investor fund",
        "RF millimeter wave 5G 6G wireless chip startup venture fund",
    ],
    "healthcare": [
        "digital health venture capital fund portfolio companies United States",
        "biotech drug discovery startup investor fund United States",
        "life sciences venture capital firm portfolio investments",
        "medical device MedTech surgical startup investor fund United States",
        "genomics precision medicine venture capital fund portfolio",
        "AI drug discovery computational biology investor fund portfolio",
        "cell gene therapy CAR-T startup venture capital fund",
        "mental health telehealth digital therapeutics investor fund",
        "medical imaging diagnostics AI startup venture capital",
        "rare disease orphan drug biopharmaceutical venture fund portfolio",
    ],
    "edtech": [
        "education technology venture capital fund portfolio United States",
        "edtech startup investor fund portfolio companies United States",
        "online learning adaptive personalized education venture capital firm",
        "K-12 education technology startup investor fund portfolio",
        "corporate learning workforce training venture capital fund",
        "AI tutoring personalized learning startup investor fund",
        "higher education university technology MOOC venture capital",
        "vocational skills coding bootcamp investor fund portfolio",
        "STEM education robotics maker startup venture capital fund",
        "language learning assessment edtech investor fund United States",
    ],
    "fintech": [
        "fintech venture capital fund portfolio companies United States",
        "payments digital banking neobank startup investor fund United States",
        "blockchain cryptocurrency DeFi web3 venture capital fund portfolio",
        "insurtech insurance technology parametric startup investor fund",
        "lending credit risk BNPL fintech venture capital firm",
        "wealth management wealthtech robo-advisor investor fund",
        "regtech compliance AML KYC financial technology venture capital",
        "embedded finance open banking banking-as-a-service startup investor",
        "quantitative trading algorithmic finance startup venture capital fund",
        "cross-border payments remittance fintech investor fund United States",
    ],
    "greentech": [
        "cleantech climate technology venture capital fund portfolio United States",
        "carbon capture direct air removal startup investor fund",
        "sustainable materials green chemistry bio-based startup venture capital",
        "circular economy recycling plastic upcycling startup investor fund",
        "water technology environmental remediation venture capital portfolio",
        "ESG impact investing venture capital fund United States portfolio",
        "bioplastics sustainable packaging startup investor fund",
        "advanced materials nanotechnology composite venture capital fund",
        "green manufacturing decarbonization industrial startup investor",
        "food agriculture agtech sustainable startup venture capital United States",
    ],
    "energy": [
        "clean energy venture capital fund portfolio companies United States",
        "renewable energy solar wind startup investor fund portfolio",
        "battery energy storage grid-scale startup venture capital United States",
        "hydrogen green fuel cell electrolysis startup investor fund",
        "nuclear SMR small modular reactor fusion startup venture capital",
        "smart grid demand response virtual power plant startup investor",
        "EV charging electric vehicle infrastructure venture capital fund",
        "solar photovoltaic perovskite startup venture capital fund portfolio",
        "biofuel biogas sustainable aviation fuel startup investor fund",
        "energy transition climate tech deep tech investor fund United States",
    ],
}

US_GEO_TERMS = {
    "united states", "usa", " u.s.", "u.s.-based", "american",
    "new york", "san francisco", "silicon valley", "boston",
    "los angeles", "chicago", "austin", "seattle", "denver",
    "miami", "atlanta", "washington dc", "menlo park", "palo alto",
    "bay area", "cambridge", "new england", "california", "new york-based",
    "sand hill", " ny,", " ca,", " tx,", " ma,", " wa,",
}

CITY_STATE_MAP = {
    "san francisco": ("CA", "San Francisco"),
    "silicon valley": ("CA", "Silicon Valley"),
    "palo alto":      ("CA", "Palo Alto"),
    "menlo park":     ("CA", "Menlo Park"),
    "los angeles":    ("CA", "Los Angeles"),
    "san jose":       ("CA", "San Jose"),
    "new york":       ("NY", "New York"),
    "nyc":            ("NY", "New York"),
    "manhattan":      ("NY", "Manhattan"),
    "boston":         ("MA", "Boston"),
    "cambridge":      ("MA", "Cambridge"),
    "chicago":        ("IL", "Chicago"),
    "austin":         ("TX", "Austin"),
    "dallas":         ("TX", "Dallas"),
    "seattle":        ("WA", "Seattle"),
    "denver":         ("CO", "Denver"),
    "miami":          ("FL", "Miami"),
    "atlanta":        ("GA", "Atlanta"),
    "washington dc":  ("DC", "Washington"),
}


# ── Timeout wrapper ────────────────────────────────────────────────────────────

def _exa_call(fn, timeout: int = EXA_TIMEOUT):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return None


# ── Field extraction ───────────────────────────────────────────────────────────

def _extract_location(text: str) -> tuple[str | None, str | None]:
    tl = text.lower()
    for city, (state, city_name) in CITY_STATE_MAP.items():
        if city in tl:
            return state, city_name
    return None, None


def _extract_founded_year(text: str) -> int | None:
    for m in re.findall(
        r"(?:founded|established|est\.?|since)\s+(?:in\s+)?(\d{4})",
        text, re.IGNORECASE,
    ):
        yr = int(m)
        if 1970 <= yr <= 2026:
            return yr
    return None


def _infer_investor_type(name: str, text: str) -> str:
    combined = (name + " " + text).lower()
    if any(k in combined for k in ["venture capital", "venture fund", "vc fund"]):
        return "VC"
    if any(k in combined for k in ["private equity", "buyout", "growth equity fund"]):
        return "PE"
    if "hedge fund" in combined or "long/short" in combined:
        return "Hedge Fund"
    if "family office" in combined:
        return "Family Office"
    if any(k in name.lower() for k in ["venture", "ventures"]):
        return "VC"
    if any(k in name.lower() for k in ["equity", "buyout"]):
        return "PE"
    return "VC"


def _get_website(url: str) -> str | None:
    if not url:
        return None
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return None


def _build_record(result, firm_name: str, sector: str) -> dict | None:
    url = result.url or ""
    title = result.title or ""
    highlights = result.highlights or []
    text_body = result.text or ""

    # Combine all available text for extraction
    combined = " ".join([title, text_body, " ".join(highlights)])
    combined_lower = combined.lower()

    # US verification — at least one geo term must appear
    if not any(term in combined_lower for term in US_GEO_TERMS):
        return None

    # Must look like an investment-related page
    invest_terms = {"invest", "portfolio", "fund", "capital", "venture",
                    "partner", "startup", "equity", "backed", "firm"}
    if not any(t in combined_lower for t in invest_terms):
        return None

    hq_state, hq_city = _extract_location(combined)
    website = _get_website(url)

    # Description: prefer highlights, fall back to extract_description
    if highlights:
        desc = " ".join(highlights)[:300].strip() or None
    else:
        desc = extract_description(text_body, firm_name) if text_body else None

    # Focus keywords from SECTOR_KEYWORD_MAP
    focus = [kw for kw in FOCUS_KEYWORDS if kw.lower() in combined_lower][:10]

    # Stage
    stages = list(dict.fromkeys(filter(None, [
        normalize_stage(m) for m in re.findall(STAGE_RE, combined)
    ])))

    firm_id = "exa_" + hashlib.md5(firm_name.lower().strip().encode()).hexdigest()[:12]

    return {
        "id": firm_id,
        "name": firm_name,
        "description": desc,
        "investor_type": _infer_investor_type(firm_name, combined),
        "sector": sector,
        "sectors": [sector],
        "stage": stages[0] if stages else None,
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
        "key_people": extract_key_people(text_body) if text_body else [],
        "portfolio": extract_portfolio(text_body) if text_body else [],
        "founded_year": _extract_founded_year(combined),
        "sec_crd": None,
        "sec_file_num": None,
        "sec_form_type": None,
        "sec_filing_url": None,
        "data_source": "exa_direct",
        "data_confidence": "medium",
        "scraped_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ── Deduplication ──────────────────────────────────────────────────────────────

def _norm_name(name: str) -> str:
    n = name.lower().strip()
    for suffix in [", llc", " llc", ", lp", " lp", ", inc.", " inc",
                   ", ltd", " ltd", ", l.p.", " l.p.", ", llp"]:
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
    return n


def _build_dedup_index(records: list[dict]) -> tuple[set[str], dict[str, list[str]]]:
    """Return (norm_set, prefix_index) for fast dedup."""
    norm_set: set[str] = set()
    prefix_index: dict[str, list[str]] = {}
    for r in records:
        n = _norm_name(r.get("name", ""))
        if not n:
            continue
        norm_set.add(n)
        prefix_index.setdefault(n[:4], []).append(n)
    return norm_set, prefix_index


def _is_duplicate(norm: str, norm_set: set[str], prefix_index: dict) -> bool:
    if norm in norm_set:
        return True
    for candidate in prefix_index.get(norm[:4], []):
        if abs(len(norm) - len(candidate)) > 8:
            continue
        if SequenceMatcher(None, norm, candidate).ratio() > 0.88:
            return True
    return False


# ── Progress ───────────────────────────────────────────────────────────────────

def _load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"done_queries": [], "new_records": []}


def _save_progress(progress: dict) -> None:
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


# ── Stats ──────────────────────────────────────────────────────────────────────

def show_stats() -> None:
    with open(WEB_JSON, encoding="utf-8") as f:
        data = json.load(f)
    sources = Counter(r.get("data_source") for r in data)
    sectors = Counter(r.get("sector") for r in data)
    print(f"\nTotal records: {len(data)}")
    print("\nBy source:")
    for s, c in sources.most_common():
        print(f"  {s:<25} {c:6}")
    print("\nBy sector:")
    for s, c in sectors.most_common():
        print(f"  {s:<20} {c:6}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Exa direct VC website scraper")
    parser.add_argument("--sectors", nargs="+", choices=list(SECTOR_QUERIES),
                        metavar="SECTOR", help="Limit to these sectors")
    parser.add_argument("--test", action="store_true",
                        help="Test mode: 2 queries per sector, 10 results each")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from saved checkpoint")
    parser.add_argument("--stats", action="store_true",
                        help="Show dataset stats and exit")
    args = parser.parse_args()

    if args.stats:
        show_stats()
        return

    if not EXA_API_KEY:
        raise SystemExit("EXA_API_KEY not set")

    exa_client = Exa(api_key=EXA_API_KEY)

    with open(WEB_JSON, encoding="utf-8") as f:
        existing = json.load(f)
    log.info("Loaded %d existing records", len(existing))

    progress = _load_progress() if args.resume else {"done_queries": [], "new_records": []}
    done_queries: set[str] = set(progress["done_queries"])
    accumulated: list[dict] = progress["new_records"]

    # Build dedup index from existing + already-accumulated
    norm_set, prefix_index = _build_dedup_index(existing + accumulated)

    sectors_to_run = args.sectors or list(SECTOR_QUERIES.keys())
    num_results = 10 if args.test else 50
    max_queries = 2 if args.test else 10

    os.makedirs(os.path.dirname(RAW_LOG), exist_ok=True)
    raw_fh = open(RAW_LOG, "a", encoding="utf-8")

    try:
        for sector in sectors_to_run:
            queries = SECTOR_QUERIES[sector][:max_queries]
            log.info("\n%s", "=" * 60)
            log.info("Sector: %-14s  %d queries × %d results",
                     sector, len(queries), num_results)
            sector_new = 0

            for qi, query in enumerate(queries, 1):
                query_key = f"{sector}::{query}"
                if query_key in done_queries:
                    log.info("  [%d/%d] SKIP (done)", qi, len(queries))
                    continue

                log.info("  [%d/%d] %s", qi, len(queries), query[:72])

                resp = _exa_call(lambda q=query: exa_client.search_and_contents(
                    q,
                    type="neural",
                    num_results=num_results,
                    exclude_domains=EXCLUDE_DOMAINS,
                    text={"max_characters": 1500},
                    highlights={"num_sentences": 4, "highlights_per_url": 3},
                ))

                if resp is None:
                    log.warning("  Timed out, skipping")
                    time.sleep(2)
                    continue

                results = resp.results
                log.info("    %d results", len(results))

                query_new = 0
                for r in results:
                    firm_name = extract_firm_name(r)
                    if not firm_name:
                        continue

                    norm = _norm_name(firm_name)
                    if _is_duplicate(norm, norm_set, prefix_index):
                        continue

                    record = _build_record(r, firm_name, sector)
                    if not record:
                        continue

                    # Register in dedup index
                    norm_set.add(norm)
                    prefix_index.setdefault(norm[:4], []).append(norm)

                    accumulated.append(record)
                    sector_new += 1
                    query_new += 1

                    # Log to raw file
                    raw_fh.write(json.dumps({
                        "firm": firm_name, "url": r.url,
                        "sector": sector,
                        "ts": datetime.utcnow().isoformat(),
                    }, ensure_ascii=False) + "\n")
                    raw_fh.flush()

                log.info("    → +%d new  (sector total so far: %d)",
                         query_new, sector_new)

                done_queries.add(query_key)
                progress["done_queries"] = list(done_queries)
                progress["new_records"] = accumulated
                _save_progress(progress)

                time.sleep(1.0)

            log.info("  %s done: +%d new records", sector, sector_new)

    finally:
        raw_fh.close()

    # ── Write output ───────────────────────────────────────────────────────────
    # Final dedup pass (accumulated may have within-run dupes from cross-sector overlap)
    final_new = []
    final_norm_set, final_prefix_index = _build_dedup_index(existing)
    for rec in accumulated:
        norm = _norm_name(rec.get("name", ""))
        if _is_duplicate(norm, final_norm_set, final_prefix_index):
            continue
        final_new.append(rec)
        final_norm_set.add(norm)
        final_prefix_index.setdefault(norm[:4], []).append(norm)

    output = existing + final_new
    with open(WEB_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info("web/investors.json → %d total records (+%d new)",
             len(output), len(final_new))

    sector_counts = Counter(r["sector"] for r in final_new)
    type_counts = Counter(r["investor_type"] for r in final_new)

    print("\n" + "=" * 60)
    print(f"New records added: {len(final_new)}")
    print(f"Total records:     {len(output)}")
    print("\nNew by sector:")
    for s, c in sector_counts.most_common():
        print(f"  {s:<20} {c:5}")
    print("\nNew by type:")
    for t, c in type_counts.most_common():
        print(f"  {t:<20} {c:5}")

    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)


if __name__ == "__main__":
    main()
