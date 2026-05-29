"""
行业标签打标脚本
Phase 1: 机构名称关键词匹配
Phase 2: EFTS ADV Brochure 全文搜索补充
输出: web/investors.json
"""

import argparse
import json
import logging
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
CSV_IN = ROOT / "data" / "adv_candidates.csv"
WEB_OUT = ROOT / "web" / "investors.json"
PROGRESS_FILE = ROOT / "data" / "label_progress.json"

# ── Keyword definitions ────────────────────────────────────────────────────────
SECTOR_KEYWORDS = {
    "ai_apps": [
        "artificial intelligence", "machine learning", "deep learning",
        "generative ai", "llm", "large language model", "enterprise ai",
        "ai saas", "foundation model", "nlp", "natural language",
        "mlops", "ai infrastructure", "computer vision", "data science",
    ],
    "ai_hardware": [
        "robotics", "robot", "autonomous vehicle", "self-driving",
        "industrial automation", "embodied ai", "edge ai", "humanoid",
        "drone", "lidar", "autonomous systems", "mechatronics",
    ],
    "semiconductor": [
        "semiconductor", "chip", "fabless", "eda", "fpga", "gpu",
        "sic", "gan", "chiplet", "integrated circuit", "wafer",
        "foundry", "microchip", "photonics", "quantum computing",
    ],
    "healthcare": [
        "health", "healthcare", "biotech", "biotechnology", "pharmaceutical",
        "pharma", "life sciences", "medical", "clinical", "therapeutics",
        "drug", "genomics", "diagnostics", "medtech", "digital health",
        "biopharma", "bioscience", "oncology", "neuroscience",
    ],
    "edtech": [
        "education", "edtech", "e-learning", "elearning", "learning",
        "workforce training", "higher education", "academic", "school",
        "university", "skill development", "online learning", "training",
    ],
    "fintech": [
        "fintech", "financial technology", "payments", "payment",
        "blockchain", "crypto", "cryptocurrency", "quantitative",
        "algorithmic", "insurtech", "wealthtech", "neobank",
        "lending", "regtech", "defi", "digital banking", "wealth management",
    ],
    "greentech": [
        "cleantech", "clean tech", "environmental", "sustainability",
        "sustainable", "carbon", "recycling", "circular economy",
        "green chemistry", "bioplastics", "waste", "water technology",
        "esg", "impact investing", "socially responsible",
    ],
    "energy": [
        "clean energy", "renewable energy", "solar", "wind energy",
        "battery", "energy storage", "hydrogen", "nuclear",
        "electric vehicle", "ev", "decarbonization", "power",
        "oil", "gas", "energy transition", "grid",
    ],
}

# Tighter keyword list for name-only matching — high-precision only, no broad terms
NAME_MATCH_KEYWORDS = {
    "ai_apps":      ["artificial intelligence", "machine learning", "deep learning",
                     "ai fund", "ai capital", "ai ventures", "ai investments"],
    "ai_hardware":  ["robotics", "autonomous vehicle", "drone fund"],
    "semiconductor":["semiconductor", "silicon valley chip", "photonics fund"],
    "healthcare":   ["health", "healthcare", "biotech", "biopharma", "pharma",
                     "life sciences", "lifesciences", "therapeutics", "genomics",
                     "oncology", "bioscience", "medtech", "neuroscience",
                     "clinical", "biomed"],
    "edtech":       ["edtech", "education fund", "education capital",
                     "education ventures", "learning ventures"],
    "fintech":      ["fintech", "payments fund", "blockchain fund", "crypto fund",
                     "insurtech", "wealthtech"],
    "greentech":    ["cleantech", "clean tech", "sustainability fund",
                     "esg fund", "impact fund", "green fund", "carbon fund",
                     "environmental fund"],
    "energy":       ["energy", "solar fund", "renewable energy", "hydrogen fund",
                     "nuclear fund", "battery fund", "wind fund",
                     "clean energy"],
}

# ── EFTS config ────────────────────────────────────────────────────────────────
EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
EFTS_HEADERS = {
    "User-Agent": "InvestorDiscoveryPlatform lipeijin0405@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}
REQUEST_DELAY = 0.25
RATE_LIMIT_SLEEP = 60
BATCH_SIZE = 100


# ── Matching logic ─────────────────────────────────────────────────────────────

def match_by_name(name: str) -> tuple[str, list[str]]:
    nl = name.lower()
    scores: dict[str, int] = {}
    for sector, kws in NAME_MATCH_KEYWORDS.items():
        hits = sum(1 for kw in kws if kw in nl)
        if hits:
            scores[sector] = hits
    if not scores:
        return "general", []
    primary = max(scores, key=scores.get)
    return primary, list(scores.keys())


def match_by_text(text: str) -> tuple[str, list[str]]:
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


def infer_investor_type(row: pd.Series, name: str) -> str:
    def y(col: str) -> bool:
        return str(row.get(col, "")).upper() == "Y"

    # ADV Item8A structured fields (most reliable)
    if y("q8a16"):
        return "VC"
    if y("q8a15"):
        return "PE"

    # Name-based signals (high confidence patterns)
    n = name.lower()
    if any(k in n for k in ["venture capital", "venture fund", "ventures fund",
                             " vc fund", " vc, llc", " vc llp"]):
        return "VC"
    if any(k in n for k in ["private equity", "pe fund", "buyout", "leveraged"]):
        return "PE"
    if any(k in n for k in ["hedge fund", "master fund", "quant fund",
                             "quantitative", "algorithmic fund"]):
        return "Hedge Fund"
    if "family office" in n:
        return "Family Office"

    # Broader name signals — require q8a1=Y or q8a2=Y context (actual fund adviser)
    has_fund = y("q8a1") or y("q8a2")
    if has_fund:
        if any(k in n for k in ["ventures", "venture", " vc "]):
            return "VC"
        if any(k in n for k in ["equity", "capital partners", "growth partners",
                                 "growth equity"]):
            return "PE"
        if any(k in n for k in ["credit", "debt", "lending", "fixed income"]):
            return "Credit Fund"

    return "Investment Adviser"


def infer_stage(name: str) -> list[str]:
    n = name.lower()
    stages = []
    if any(k in n for k in ["seed", "early stage", "pre-seed"]):
        stages.append("Seed")
    if any(k in n for k in ["series a", "series-a"]):
        stages.append("Series A")
    if any(k in n for k in ["growth", "expansion"]):
        stages.append("Growth")
    if any(k in n for k in ["buyout", "lbo"]):
        stages.append("Buyout")
    if any(k in n for k in ["multi", "all stage"]):
        stages.append("Multi-Stage")
    return stages if stages else ["Unknown"]


def build_record(row: pd.Series, sector: str, sectors: list[str]) -> dict:
    crd = str(row["crd"])
    sec_nb = str(row.get("sec_nb", "") or "")
    name_raw = str(row["name"])
    name = name_raw.title()

    # CIK from sec_nb (e.g. "801-12345" → strip prefix, zero-pad)
    cik = ""
    if sec_nb and "-" in sec_nb:
        cik = sec_nb.split("-")[-1].strip().zfill(10)

    web_raw = row.get("website")
    web = None
    if web_raw and str(web_raw).lower() not in ("", "nan", "none", "n/a"):
        web = str(web_raw).strip()
        if web and not web.lower().startswith("http"):
            web = "https://" + web

    aum = None
    try:
        aum_val = row.get("aum_usd")
        if aum_val and str(aum_val) not in ("", "0", "nan"):
            aum = int(float(str(aum_val)))
            if aum == 0:
                aum = None
    except (ValueError, TypeError):
        aum = None

    investor_type = infer_investor_type(row, name_raw)
    stage = infer_stage(name_raw)

    return {
        "id": f"adv_{crd}",
        "name": name,
        "description": None,
        "investor_type": investor_type,
        "sector": sector,
        "sectors": sectors if sectors else [sector],
        "stage": stage,
        "focus": [],
        "region": "North America",
        "hq_state": str(row.get("state", "") or ""),
        "hq_city": None,
        "hq_country": "US",
        "us_verified": True,
        "website": web,
        "aum_usd": aum,
        "fund_size_usd": None,
        "check_size_min_usd": None,
        "check_size_max_usd": None,
        "key_people": [],
        "portfolio": [],
        "founded_year": None,
        "sec_crd": crd,
        "sec_file_num": sec_nb,
        "sec_form_type": "ADV",
        "sec_filing_url": (
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=ADV"
            if cik else None
        ),
        "data_source": "sec_adv_bulk",
        "data_confidence": "high",
        "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ── EFTS fetch ─────────────────────────────────────────────────────────────────

_efts_session = requests.Session()
_efts_session.headers.update(EFTS_HEADERS)


def fetch_brochure_text(name: str) -> str:
    params = {
        "q": f'"{name}"',
        "forms": "ADV",
        "dateRange": "custom",
        "startdt": "2020-01-01",
        "enddt": "2026-12-31",
        "from": "0",
    }
    for attempt in range(3):
        try:
            r = _efts_session.get(EFTS_URL, params=params, timeout=12)
            if r.status_code == 429:
                log.warning(f"  EFTS 429 — sleeping {RATE_LIMIT_SLEEP}s")
                time.sleep(RATE_LIMIT_SLEEP)
                continue
            if r.status_code != 200:
                return ""
            data = r.json()
            hits = data.get("hits", {}).get("hits", [])
            if not hits:
                return ""
            hit = hits[0]
            src = hit.get("_source", {})
            hl = hit.get("highlight", {})
            parts = [src.get("entity_name", ""), src.get("period_of_report", "")]
            for v in hl.values():
                if isinstance(v, list):
                    parts.extend(v)
            return " ".join(str(p) for p in parts if p)
        except requests.exceptions.RequestException as e:
            log.warning(f"  EFTS request error: {e}")
            time.sleep(2 ** attempt)
    return ""


# ── Progress helpers ───────────────────────────────────────────────────────────

def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"completed_crds": [], "phase1_records": [], "phase2_sectors": {}}


def save_progress(prog: dict) -> None:
    with open(PROGRESS_FILE, "w") as f:
        json.dump(prog, f)


# ── Phase runners ──────────────────────────────────────────────────────────────

def run_phase1(df: pd.DataFrame) -> tuple[list[dict], list[pd.Series]]:
    """Name matching. Returns (labeled_records, unlabeled_rows)."""
    log.info("=== Phase 1: Name keyword matching ===")
    labeled: list[dict] = []
    unlabeled: list[pd.Series] = []

    for _, row in df.iterrows():
        name = str(row["name"])
        sector, sectors = match_by_name(name)
        if sector == "general":
            unlabeled.append(row)
        else:
            labeled.append(build_record(row, sector, sectors))

    log.info(f"  Labeled:   {len(labeled):,}")
    log.info(f"  Unlabeled: {len(unlabeled):,} → Phase 2")
    sector_c = Counter(r["sector"] for r in labeled)
    for s, c in sector_c.most_common():
        log.info(f"    {s:<22} {c:,}")
    return labeled, unlabeled


def run_phase2(unlabeled: list[pd.Series], resume_crds: set[str],
               phase2_sectors: dict) -> list[dict]:
    """EFTS brochure search for unmatched firms."""
    log.info(f"=== Phase 2: EFTS brochure search ({len(unlabeled):,} firms) ===")
    records: list[dict] = []
    newly_labeled = 0
    still_general = 0

    for i, row in enumerate(unlabeled):
        crd = str(row["crd"])
        name = str(row["name"])

        # Resume: use saved sector result if available
        if crd in phase2_sectors:
            sector = phase2_sectors[crd]["sector"]
            sectors = phase2_sectors[crd]["sectors"]
            records.append(build_record(row, sector, sectors))
            if sector != "general":
                newly_labeled += 1
            else:
                still_general += 1
            continue

        if crd in resume_crds:
            # Was processed but result not in phase2_sectors → treat as general
            records.append(build_record(row, "general", []))
            still_general += 1
            continue

        # Fetch brochure text
        time.sleep(REQUEST_DELAY)
        text = fetch_brochure_text(name)
        sector, sectors = match_by_text(text) if text else ("general", [])

        phase2_sectors[crd] = {"sector": sector, "sectors": sectors}
        resume_crds.add(crd)
        records.append(build_record(row, sector, sectors))

        if sector != "general":
            newly_labeled += 1
        else:
            still_general += 1

        if (i + 1) % BATCH_SIZE == 0:
            log.info(f"  {i+1:>5}/{len(unlabeled)} | labeled {newly_labeled} | "
                     f"general {still_general}")
            save_progress({
                "completed_crds": list(resume_crds),
                "phase2_sectors": phase2_sectors,
            })

    log.info(f"  Newly labeled: {newly_labeled:,}")
    log.info(f"  Still general: {still_general:,}")
    sector_c = Counter(r["sector"] for r in records)
    for s, c in sector_c.most_common():
        log.info(f"    {s:<22} {c:,}")
    return records


# ── Stats ──────────────────────────────────────────────────────────────────────

def print_final_stats(records: list[dict],
                      phase1_count: int, phase1_labeled: int,
                      phase2_labeled: int) -> None:
    total = len(records)
    sector_c = Counter(r["sector"] for r in records)
    type_c = Counter(r["investor_type"] for r in records)
    has_aum = sum(1 for r in records if r.get("aum_usd"))
    has_web = sum(1 for r in records if r.get("website"))
    still_gen = sector_c.get("general", 0)

    print("\n" + "=" * 60)
    print("行业标签打标完成")
    print("=" * 60)
    print(f"总机构数:              {total:>10,}")
    print(f"\n阶段一（名称匹配）:")
    print(f"  命中行业:            {phase1_labeled:>10,} ({phase1_labeled*100//total}%)")
    print(f"  未命中(general):     {phase1_count - phase1_labeled:>10,} → 进入阶段二")
    if phase2_labeled >= 0:
        print(f"\n阶段二（EFTS补充）:")
        print(f"  成功补充标签:        {phase2_labeled:>10,}")
        print(f"  仍为 general:        {still_gen:>10,} ({still_gen*100//total}%)")
    print(f"\n行业分布:")
    bar_max = max(sector_c.values()) if sector_c else 1
    for s, c in sector_c.most_common():
        bar = "█" * int(c * 30 / bar_max)
        print(f"  {s:<22} {c:>6,}  {bar}")
    print(f"\n投资人类型:")
    for t, c in type_c.most_common():
        print(f"  {t:<25} {c:>6,}")
    print(f"\n有 AUM 数据:           {has_aum:>10,} ({has_aum*100//total}%)")
    print(f"有网站:                {has_web:>10,} ({has_web*100//total}%)")
    print(f"\n输出: {WEB_OUT} ({total:,} 条)")
    print("=" * 60)


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", type=int, choices=[1, 2])
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--output-only", action="store_true")
    args = parser.parse_args()

    df = pd.read_csv(CSV_IN, dtype=str)
    log.info(f"Loaded {len(df):,} firms from {CSV_IN}")

    progress = load_progress() if args.resume else {
        "completed_crds": [],
        "phase2_sectors": {},
    }
    resume_crds = set(progress.get("completed_crds", []))
    phase2_sectors: dict = progress.get("phase2_sectors", {})

    if args.output_only:
        # Re-build output from saved phase2 data + phase1 re-run
        p1_records, unlabeled = run_phase1(df)
        p2_records = [
            build_record(
                row,
                phase2_sectors.get(str(row["crd"]), {}).get("sector", "general"),
                phase2_sectors.get(str(row["crd"]), {}).get("sectors", []),
            )
            for row in unlabeled
        ]
        all_records = p1_records + p2_records
        p2_labeled = sum(1 for r in p2_records if r["sector"] != "general")
        _write_and_report(all_records, len(df), len(p1_records),
                          len(unlabeled), p2_labeled)
        return

    p1_records, unlabeled = run_phase1(df)
    phase1_labeled = len(p1_records)
    p2_labeled = -1

    if args.phase == 1:
        # Phase 1 only: write with unlabeled as general
        p2_records = [build_record(row, "general", []) for row in unlabeled]
        all_records = p1_records + p2_records
        _write_and_report(all_records, len(df), phase1_labeled,
                          len(unlabeled), -1)
        return

    # Phase 2 (either --phase 2 or full run)
    p2_records = run_phase2(unlabeled, resume_crds, phase2_sectors)
    save_progress({"completed_crds": list(resume_crds), "phase2_sectors": phase2_sectors})
    p2_labeled = sum(1 for r in p2_records if r["sector"] != "general")

    all_records = p1_records + p2_records
    _write_and_report(all_records, len(df), phase1_labeled, len(unlabeled), p2_labeled)


def _write_and_report(records: list[dict], total_in: int,
                      phase1_labeled: int, phase2_total: int,
                      phase2_labeled: int) -> None:
    with open(WEB_OUT, "w") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    log.info(f"Written {len(records):,} records → {WEB_OUT}")
    print_final_stats(records, phase2_total, phase1_labeled, phase2_labeled)


if __name__ == "__main__":
    main()
