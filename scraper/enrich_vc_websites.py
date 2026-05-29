"""
VC/PE 网站抓取富化脚本
对 investor_type ∈ {VC, PE} 的机构抓取官网，原地更新 web/investors.json
"""

import argparse
import json
import logging
import os
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
INVESTORS_JSON = ROOT / "web" / "investors.json"
PROGRESS_FILE  = ROOT / "data" / "enrich_progress.json"
ERROR_LOG      = ROOT / "data" / "enrich_errors.log"

# ── HTTP config ────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
REQUEST_DELAY   = 1.2
REQUEST_TIMEOUT = 10
MAX_TEXT_CHARS  = 6000
MAX_PAGES       = 3

TARGET_PATHS = [
    "",
    "/about",
    "/about-us",
    "/portfolio",
    "/investments",
    "/team",
    "/people",
    "/focus",
    "/strategy",
    "/approach",
]

# ── Sector keywords ────────────────────────────────────────────────────────────
SECTOR_KEYWORDS = {
    "ai_apps": [
        "artificial intelligence", "machine learning", "deep learning",
        "generative ai", "llm", "large language model", "enterprise ai",
        "ai saas", "foundation model", "natural language processing",
        "mlops", "ai infrastructure", "computer vision", "data science",
    ],
    "ai_hardware": [
        "robotics", "autonomous vehicle", "self-driving",
        "industrial automation", "embodied ai", "edge ai", "humanoid",
        "drone", "lidar", "autonomous systems",
    ],
    "semiconductor": [
        "semiconductor", "chip design", "fabless", "eda tools", "fpga",
        "gpu design", "chiplet", "integrated circuit", "wafer fabrication",
        "photonics", "quantum computing",
    ],
    "healthcare": [
        "healthcare", "biotech", "biotechnology", "pharmaceutical",
        "pharma", "life sciences", "medical device", "clinical",
        "therapeutics", "drug discovery", "genomics", "diagnostics",
        "medtech", "biopharma", "bioscience", "oncology", "neuroscience",
        "digital health",
    ],
    "edtech": [
        "education technology", "edtech", "e-learning", "learning platform",
        "workforce training", "higher education", "online learning",
        "learning management",
    ],
    "fintech": [
        "fintech", "financial technology", "payments technology",
        "blockchain", "cryptocurrency", "quantitative trading",
        "insurtech", "wealthtech", "neobank", "lending technology",
        "regtech", "defi", "digital banking",
    ],
    "greentech": [
        "cleantech", "clean tech", "environmental technology",
        "sustainability", "carbon capture", "recycling technology",
        "circular economy", "green chemistry", "bioplastics",
        "esg investing", "impact investing", "climate tech",
    ],
    "energy": [
        "clean energy", "renewable energy", "solar energy", "wind energy",
        "battery storage", "energy storage", "green hydrogen", "nuclear energy",
        "electric vehicle", "decarbonization", "energy transition",
        "smart grid", "offshore wind",
    ],
}

STAGE_KEYWORDS = {
    "Pre-Seed": ["pre-seed", "pre seed", "idea stage", "inception"],
    "Seed":     ["seed stage", "seed fund", "seed-stage", "seed capital",
                 "seed investment", "early stage"],
    "Series A": ["series a", "series-a"],
    "Series B": ["series b"],
    "Series C+":["series c", "late stage", "pre-ipo"],
    "Growth":   ["growth equity", "growth capital", "growth stage",
                 "expansion stage"],
    "Buyout":   ["buyout", "leveraged buyout", "lbo", "management buyout"],
    "Multi-Stage":["multi-stage", "all stages", "across stages",
                   "any stage", "various stages"],
}

TITLES = [
    "Founder & CEO", "Co-Founder & CEO", "Managing General Partner",
    "General Partner", "Managing Partner", "Partner",
    "Managing Director", "Co-Founder", "Founder",
    "Principal", "Vice President", "Senior Associate", "Associate",
    "CEO", "CIO", "CFO", "President", "Director",
]


# ── Fetch helpers ──────────────────────────────────────────────────────────────

SKIP_DOMAINS = {
    "linkedin.com", "twitter.com", "x.com", "facebook.com",
    "instagram.com", "youtube.com", "crunchbase.com", "bloomberg.com",
    "pitchbook.com", "instagram.com", "t.co",
}


def _make_session() -> requests.Session:
    s = requests.Session()
    # Keep system proxy (Clash/etc.) so normal websites are reachable
    s.headers.update(HEADERS)
    return s


def _normalize_url(raw: str) -> str:
    """Lowercase scheme+host, fix common issues."""
    if not raw:
        return ""
    url = raw.strip().lower()
    if not url.startswith("http"):
        url = "https://" + url.lstrip("/")
    # Remove double slashes except after scheme
    url = re.sub(r"(?<!:)//+", "/", url)
    return url


def _should_skip(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower().lstrip("www.")
        return any(host == d or host.endswith("." + d) for d in SKIP_DOMAINS)
    except Exception:
        return False

SESSION = _make_session()


def fetch_page(url: str) -> tuple[str, BeautifulSoup | None]:
    """Return (plain_text, soup). plain_text capped at MAX_TEXT_CHARS."""
    try:
        r = SESSION.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return "", None
        ct = r.headers.get("Content-Type", "")
        if "html" not in ct and "text" not in ct:
            return "", None
        soup = BeautifulSoup(r.text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)[:MAX_TEXT_CHARS]
        return text, soup
    except Exception as e:
        return "", None


def try_pages(base_url: str) -> tuple[str, BeautifulSoup | None]:
    """Try up to MAX_PAGES paths; return concatenated text + last successful soup."""
    if not base_url:
        return "", None
    base = _normalize_url(base_url).rstrip("/")

    texts: list[str] = []
    last_soup: BeautifulSoup | None = None
    fetched = 0

    for path in TARGET_PATHS:
        if fetched >= MAX_PAGES:
            break
        url = base + path
        text, soup = fetch_page(url)
        if text:
            texts.append(text)
            if soup:
                last_soup = soup
            fetched += 1
        time.sleep(REQUEST_DELAY)

    return " ".join(texts), last_soup


# ── Extraction logic ───────────────────────────────────────────────────────────

def extract_sectors(text: str) -> tuple[str, list[str], list[str]]:
    tl = text.lower()
    scores: dict[str, int] = {}
    matched: dict[str, list[str]] = {}
    for sector, kws in SECTOR_KEYWORDS.items():
        hits = [kw for kw in kws if kw in tl]
        if hits:
            scores[sector] = len(hits)
            matched[sector] = hits
    if not scores:
        return "general", [], []
    primary = max(scores, key=scores.get)
    all_sectors = sorted(scores, key=scores.get, reverse=True)
    focus = matched.get(primary, [])[:10]
    return primary, all_sectors, focus


def extract_stages(text: str) -> list[str]:
    tl = text.lower()
    found = [s for s, kws in STAGE_KEYWORDS.items() if any(kw in tl for kw in kws)]
    return found if found else ["Unknown"]


def _is_likely_name(s: str) -> bool:
    """Heuristic: 2-4 words, each starting with uppercase."""
    words = s.split()
    if not 2 <= len(words) <= 4:
        return False
    if not all(w and w[0].isupper() for w in words):
        return False
    # Reject if it looks like a heading or nav link
    stopwords = {"about", "contact", "home", "portfolio", "investments",
                 "team", "our", "the", "and", "more", "learn", "view",
                 "read", "back", "next", "menu", "follow"}
    if any(w.lower() in stopwords for w in words):
        return False
    return True


def extract_key_people(soup: BeautifulSoup | None) -> list[dict]:
    if soup is None:
        return []
    people: list[dict] = []

    # Look for team/people sections
    team_containers = soup.find_all(
        lambda t: t.name in ("section", "div", "article") and
        any(kw in " ".join(t.get("class", [])).lower() + t.get("id", "").lower()
            for kw in ("team", "people", "partner", "leadership", "staff", "member")),
        limit=4,
    )

    for container in team_containers:
        items = container.find_all(
            lambda t: t.name in ("div", "article", "li", "figure"),
            limit=30,
        )
        for item in items:
            lines = [l.strip() for l in
                     item.get_text(separator="\n", strip=True).split("\n")
                     if l.strip()]
            if len(lines) < 2:
                continue
            name_cand = lines[0]
            if not _is_likely_name(name_cand):
                continue
            title_cand = lines[1] if len(lines) > 1 else ""
            matched_title = next(
                (t for t in TITLES if t.lower() in title_cand.lower()), None
            )
            if matched_title:
                entry = {"name": name_cand, "title": matched_title}
                if entry not in people:
                    people.append(entry)
            if len(people) >= 10:
                break
        if len(people) >= 10:
            break

    return people


def extract_portfolio(soup: BeautifulSoup | None, text: str) -> list[str]:
    if soup is None:
        return []
    portfolio: list[str] = []

    # Portfolio sections by class/id
    containers = soup.find_all(
        lambda t: t.name in ("section", "div", "ul") and
        any(kw in " ".join(t.get("class", [])).lower() + t.get("id", "").lower()
            for kw in ("portfolio", "investment", "compan", "startup",
                       "company", "backed", "founded")),
        limit=4,
    )
    for container in containers:
        for item in container.find_all(["a", "h3", "h4", "h2", "li", "span"],
                                       limit=30):
            name = item.get_text(strip=True)
            if (3 <= len(name) <= 50 and
                not any(nav in name.lower()
                        for nav in ("home", "about", "contact", "team",
                                    "blog", "news", "learn more", "read",
                                    "view all", "see all"))):
                portfolio.append(name)

    # Deduplicate preserving order
    seen: set[str] = set()
    result: list[str] = []
    for p in portfolio:
        if p not in seen:
            seen.add(p)
            result.append(p)
        if len(result) >= 20:
            break
    return result


# ── Progress helpers ───────────────────────────────────────────────────────────

def load_progress() -> set[str]:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return set(json.load(f).get("completed_ids", []))
    return set()


def save_progress(investors: list[dict], completed: set[str]) -> None:
    with open(PROGRESS_FILE, "w") as f:
        json.dump({"completed_ids": list(completed)}, f)
    with open(INVESTORS_JSON, "w", encoding="utf-8") as f:
        json.dump(investors, f, ensure_ascii=False, indent=2)


def log_error(inv_id: str, name: str, url: str, reason: str) -> None:
    with open(ERROR_LOG, "a") as f:
        f.write(f"{datetime.now().isoformat()} | {inv_id} | {name[:40]} | "
                f"{url} | {reason}\n")


# ── Stats ──────────────────────────────────────────────────────────────────────

def print_stats(investors: list[dict]) -> None:
    vc_pe = [r for r in investors if r.get("investor_type") in ("VC", "PE")]
    total = len(vc_pe)
    if not total:
        print("No VC/PE records found.")
        return

    with_web = sum(1 for r in vc_pe if r.get("website"))
    labeled  = sum(1 for r in vc_pe if r.get("sector") != "general")
    with_port= sum(1 for r in vc_pe if r.get("portfolio"))
    with_ppl = sum(1 for r in vc_pe if r.get("key_people"))
    port_sizes = [len(r["portfolio"]) for r in vc_pe if r.get("portfolio")]
    ppl_sizes  = [len(r["key_people"]) for r in vc_pe if r.get("key_people")]
    sector_c = Counter(r["sector"] for r in vc_pe)

    print("\n" + "=" * 60)
    print("VC/PE 网站抓取完成")
    print("=" * 60)
    print(f"处理机构总数:        {total} 家")
    print(f"有网站:              {with_web} 家 ({with_web*100//total}%)")
    print(f"行业成功打标:        {labeled} 家 ({labeled*100//total}%)")
    print(f"有 portfolio 数据:   {with_port} 家")
    print(f"有团队数据:          {with_ppl} 家")
    if port_sizes:
        print(f"平均 portfolio 数:   {sum(port_sizes)/len(port_sizes):.1f}")
    if ppl_sizes:
        print(f"平均团队人数:        {sum(ppl_sizes)/len(ppl_sizes):.1f}")
    print(f"\n行业分布 (VC/PE 子集):")
    for s, c in sector_c.most_common():
        print(f"  {s:<22} {c}")
    print(f"\nweb/investors.json 已更新 ({len(investors):,} 条)")
    print("=" * 60)


# ── Main enrichment loop ───────────────────────────────────────────────────────

def enrich(investors: list[dict], completed: set[str],
           test_mode: bool = False) -> list[dict]:
    targets = [(i, inv) for i, inv in enumerate(investors)
               if inv.get("investor_type") in ("VC", "PE")]

    if test_mode:
        targets = targets[:10]

    log.info(f"VC/PE targets: {len(targets)} | already done: {len(completed)}")

    accessible = 0
    labeled_new = 0

    for count, (idx, inv) in enumerate(targets, 1):
        inv_id  = inv.get("id", "")
        name    = inv.get("name", "")
        website = inv.get("website", "")

        if inv_id in completed:
            continue

        if not website:
            log.info(f"[{count}/{len(targets)}] {name[:45]} — no website, skip")
            log_error(inv_id, name, "", "no website")
            completed.add(inv_id)
            continue

        website = _normalize_url(website)

        if _should_skip(website):
            log.info(f"[{count}/{len(targets)}] {name[:45]} — social/aggregator URL, skip")
            log_error(inv_id, name, website, "skip: social/aggregator")
            completed.add(inv_id)
            continue

        log.info(f"[{count}/{len(targets)}] {name[:45]} — {website}")

        full_text, soup = try_pages(website)

        if not full_text:
            log.info(f"  unreachable")
            log_error(inv_id, name, website, "unreachable")
            completed.add(inv_id)
            continue

        accessible += 1

        # Extract
        sector, sectors, focus = extract_sectors(full_text)
        portfolio  = extract_portfolio(soup, full_text)
        key_people = extract_key_people(soup)
        stages     = extract_stages(full_text)

        log.info(f"  sector={sector} portfolio={len(portfolio)} people={len(key_people)}")

        # Update only fields where we found new data
        if sector != "general":
            investors[idx]["sector"]  = sector
            investors[idx]["sectors"] = sectors
            labeled_new += 1
        if focus:
            investors[idx]["focus"] = focus
        if portfolio:
            investors[idx]["portfolio"] = portfolio
        if key_people:
            investors[idx]["key_people"] = key_people
        if stages != ["Unknown"]:
            investors[idx]["stage"] = stages

        completed.add(inv_id)

        if count % 20 == 0:
            save_progress(investors, completed)
            log.info(f"  checkpoint saved ({count} done)")

    return investors


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--test",   action="store_true",
                        help="Only process first 10 VC/PE firms")
    parser.add_argument("--stats",  action="store_true",
                        help="Print stats from current investors.json, no scraping")
    args = parser.parse_args()

    with open(INVESTORS_JSON, encoding="utf-8") as f:
        investors = json.load(f)
    log.info(f"Loaded {len(investors):,} records from {INVESTORS_JSON}")

    if args.stats:
        print_stats(investors)
        return

    completed = load_progress() if args.resume else set()
    if args.resume:
        log.info(f"Resuming — {len(completed)} already done")

    investors = enrich(investors, completed, test_mode=args.test)

    # Final save
    save_progress(investors, completed)
    print_stats(investors)


if __name__ == "__main__":
    main()
