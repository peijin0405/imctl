"""
merge_final.py
Cleans and standardizes web/investors.json into a final production dataset.

Outputs:
  data/investors_final.json  — full cleaned dataset
  web/investors.json         — overwrite in place (same file, cleaned)
"""

import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

SRC  = os.path.join(os.path.dirname(__file__), "..", "web",  "investors.json")
DEST = os.path.join(os.path.dirname(__file__), "..", "data", "investors_final.json")
WEB  = SRC  # overwrite in place


# ── Lookup tables ─────────────────────────────────────────────────────────────

TYPE_MAP = {
    "vc":                  "VC",
    "venture":             "VC",
    "venture capital":     "VC",
    "pe":                  "PE",
    "private equity":      "PE",
    "growth equity":       "PE",
    "hedge fund":          "Hedge Fund",
    "hedge":               "Hedge Fund",
    "family office":       "Family Office",
    "credit fund":         "Credit Fund",
    "investment adviser":  "Investment Adviser",
    "investment advisor":  "Investment Adviser",
    "cvc":                 "Corporate VC",
    "corporate vc":        "Corporate VC",
    "corporate venture":   "Corporate VC",
}

VALID_TYPES = {
    "VC", "PE", "Hedge Fund", "Family Office",
    "Credit Fund", "Investment Adviser", "Corporate VC",
}

VALID_SECTORS = {
    "ai_apps", "ai_hardware", "semiconductor", "healthcare",
    "edtech", "fintech", "greentech", "energy", "general",
}

STATE_ABBREVS = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN",
    "IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV",
    "NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN",
    "TX","UT","VT","VA","WA","WV","WI","WY","DC",
}

# Completeness scoring weights (must sum to 100)
COMPLETENESS_WEIGHTS = {
    "name":         10,
    "website":      10,
    "hq_state":      8,
    "investor_type": 8,
    "sector":        8,
    "aum_usd":      10,
    "description":   8,
    "key_people":    8,
    "portfolio":     8,
    "contacts":     10,
    "founded_year":  5,
    "focus":         7,
}


# ── Field transformers ────────────────────────────────────────────────────────

def make_slug(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    s = re.sub(r"-+", "-", s)
    return s[:80]


def format_aum(usd) -> str | None:
    if not usd:
        return None
    try:
        usd = float(usd)
    except (TypeError, ValueError):
        return None
    if usd >= 1e9:
        return f"${usd / 1e9:.1f}B"
    if usd >= 1e6:
        return f"${usd / 1e6:.0f}M"
    if usd >= 1e3:
        return f"${usd / 1e3:.0f}K"
    return f"${usd:.0f}"


def format_check(min_usd, max_usd) -> str | None:
    if not min_usd and not max_usd:
        return None
    mn = format_aum(min_usd)
    mx = format_aum(max_usd)
    if mn and mx:
        return f"{mn}–{mx}"
    if mn:
        return f"{mn}+"
    return f"up to {mx}"


def normalize_stage(stage) -> list:
    if not stage:
        return []
    if isinstance(stage, list):
        return [s.strip() for s in stage if s and str(s).strip()]
    if isinstance(stage, str) and stage.strip():
        return [stage.strip()]
    return []


def normalize_website(url) -> str | None:
    if not url:
        return None
    url = str(url).strip()
    if not url or url.lower() in ("null", "none", "n/a", "-"):
        return None
    if url.lower().startswith("http://") or url.lower().startswith("https://"):
        # Lowercase the scheme+host portion only
        try:
            from urllib.parse import urlparse, urlunparse
            p = urlparse(url)
            return urlunparse((
                p.scheme.lower(),
                p.netloc.lower(),
                p.path,
                p.params,
                p.query,
                p.fragment,
            ))
        except Exception:
            return url
    if url.startswith("//"):
        return "https:" + url
    return "https://" + url


def normalize_type(t) -> str:
    if not t:
        return "Investment Adviser"
    mapped = TYPE_MAP.get(str(t).lower().strip())
    if mapped:
        return mapped
    # Preserve if already a known value
    if t in VALID_TYPES:
        return t
    return "Investment Adviser"


def normalize_name(name: str) -> str:
    """Strip, normalize whitespace, title-case only if ALLCAPS."""
    if not name:
        return ""
    name = " ".join(name.split())  # collapse internal whitespace
    # If entirely uppercase (common in SEC data), convert to title case
    if name == name.upper() and len(name) > 2:
        # Preserve common acronyms: LLC, LP, LLP, PC, NA, etc.
        words = []
        for w in name.split():
            if w in ("LLC", "LP", "LLP", "PC", "NA", "II", "III", "IV",
                     "VI", "VII", "VIII", "IX", "XI", "XII", "GP", "VC",
                     "PE", "AI", "US", "USA", "UK", "ETF", "AUM", "CVC"):
                words.append(w)
            else:
                words.append(w.capitalize())
        return " ".join(words)
    return name


def normalize_state(state) -> str | None:
    if not state:
        return None
    s = str(state).strip().upper()
    if s in STATE_ABBREVS:
        return s
    return None


def normalize_city(city) -> str | None:
    if not city:
        return None
    city = str(city).strip()
    if city.lower() in ("null", "none", "n/a", "-", ""):
        return None
    return city.title()


def normalize_focus(focus) -> list:
    if not focus:
        return []
    if isinstance(focus, list):
        return [str(f).strip() for f in focus if f and str(f).strip()]
    if isinstance(focus, str) and focus.strip():
        return [focus.strip()]
    return []


def normalize_key_people(people) -> list:
    if not people:
        return []
    result = []
    for p in people:
        if isinstance(p, dict):
            name = str(p.get("name") or "").strip()
            title = str(p.get("title") or "").strip()
            if name:
                result.append({"name": name, "title": title})
        elif isinstance(p, str) and p.strip():
            result.append({"name": p.strip(), "title": ""})
    return result


def normalize_contacts(contacts) -> list:
    if not contacts:
        return []
    result = []
    for c in contacts:
        if not isinstance(c, dict):
            continue
        entry = {
            "name":       (c.get("name") or "").strip() or None,
            "title":      (c.get("title") or "").strip() or None,
            "email":      (c.get("email") or "").strip() or None,
            "phone":      (c.get("phone") or "").strip() or None,
            "linkedin":   (c.get("linkedin") or "").strip() or None,
            "confidence": c.get("confidence") or "low",
        }
        # Drop entirely empty entries
        if any(entry[k] for k in ("name", "email", "phone", "linkedin")):
            result.append(entry)
    return result


def normalize_portfolio(portfolio) -> list:
    if not portfolio:
        return []
    if isinstance(portfolio, list):
        return [str(p).strip() for p in portfolio if p and str(p).strip()]
    return []


def normalize_sectors(sector, sectors) -> list:
    if isinstance(sectors, list) and sectors:
        valid = [s for s in sectors if s in VALID_SECTORS]
        if valid:
            return valid
    if sector and sector in VALID_SECTORS:
        return [sector]
    return ["general"]


def normalize_aum(aum) -> int | None:
    if aum is None:
        return None
    try:
        v = int(float(aum))
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def normalize_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def calc_completeness(d: dict) -> int:
    score = 0
    for field, weight in COMPLETENESS_WEIGHTS.items():
        val = d.get(field)
        if val and val not in ([], {}, "", None):
            score += weight
    return min(score, 100)


def make_id(record: dict) -> str:
    """Generate a stable hash ID if none exists."""
    seed = (record.get("name", "") + (record.get("hq_state") or "")).lower()
    return "m_" + hashlib.md5(seed.encode()).hexdigest()[:12]


# ── Main cleaner ──────────────────────────────────────────────────────────────

def clean_record(raw: dict) -> dict:
    # Name
    name = normalize_name(str(raw.get("name") or "").strip())
    if not name:
        return None  # skip nameless records

    # Sector
    raw_sector = raw.get("sector") or "general"
    sector = raw_sector if raw_sector in VALID_SECTORS else "general"
    sectors = normalize_sectors(sector, raw.get("sectors"))

    # Stage
    stage = normalize_stage(raw.get("stage"))

    # AUM
    aum_usd = normalize_aum(raw.get("aum_usd"))

    # Check sizes
    check_min = normalize_int(raw.get("check_size_min_usd"))
    check_max = normalize_int(raw.get("check_size_max_usd"))

    # Website
    website = normalize_website(raw.get("website") or raw.get("source_url"))

    # State / city
    hq_state = normalize_state(raw.get("hq_state"))
    hq_city  = normalize_city(raw.get("hq_city"))

    # Investor type
    investor_type = normalize_type(raw.get("investor_type"))

    # ID
    rec_id = str(raw.get("id") or "").strip() or make_id({"name": name, "hq_state": hq_state})

    # Build cleaned record
    cleaned = {
        # Identity
        "id":               rec_id,
        "name":             name,
        "slug":             make_slug(name),

        # Classification
        "investor_type":    investor_type,
        "sector":           sector,
        "sectors":          sectors,
        "stage":            stage,
        "focus":            normalize_focus(raw.get("focus")),

        # Geography
        "region":           "North America",
        "hq_country":       "US",
        "hq_state":         hq_state,
        "hq_city":          hq_city,

        # Scale
        "aum_usd":          aum_usd,
        "aum_display":      format_aum(aum_usd),
        "check_size_min_usd": check_min,
        "check_size_max_usd": check_max,
        "check_size_display": format_check(check_min, check_max),

        # Contact & web
        "website":          website,
        "sec_filing_url":   raw.get("sec_filing_url") or None,

        # People & investments
        "key_people":       normalize_key_people(raw.get("key_people")),
        "contacts":         normalize_contacts(raw.get("contacts")),
        "portfolio":        normalize_portfolio(raw.get("portfolio")),

        # Description
        "description":      (str(raw.get("description") or "").strip() or None),
        "founded_year":     normalize_int(raw.get("founded_year")),

        # SEC metadata
        "sec_crd":          (str(raw.get("sec_crd") or "").strip() or None),
        "sec_file_num":     (str(raw.get("sec_file_num") or "").strip() or None),

        # Data quality
        "data_source":      str(raw.get("data_source") or "unknown"),
        "data_confidence":  raw.get("data_confidence") or "medium",
        "us_verified":      True,
        "scraped_at":       raw.get("scraped_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    cleaned["completeness_score"] = calc_completeness(cleaned)
    return cleaned


# ── Dedup ─────────────────────────────────────────────────────────────────────

def dedup(records: list[dict]) -> tuple[list[dict], int]:
    """
    Deduplicate by (normalized_name, hq_state).
    When two records share a key, keep the one with the higher completeness_score.
    """
    seen: dict[tuple, dict] = {}
    for r in records:
        norm = re.sub(r"\s+", " ", r["name"].lower().strip())
        # Strip common legal suffixes for matching
        for sfx in (" llc", " lp", " l.p.", " inc", " ltd", " limited",
                    " partners", " capital", " fund", " ventures", " venture",
                    " management", " advisors", " group", " investments"):
            norm = norm.replace(sfx, "")
        norm = norm.strip()
        key = (norm, r.get("hq_state") or "")
        existing = seen.get(key)
        if existing is None or r["completeness_score"] > existing["completeness_score"]:
            seen[key] = r

    deduped = list(seen.values())
    removed = len(records) - len(deduped)
    return deduped, removed


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(raw_count: int, cleaned: list[dict], removed_nulls: int, removed_dups: int):
    total = len(cleaned)
    sep = "=" * 60

    focused = [d for d in cleaned if d["sector"] != "general"]
    sources = Counter(d["data_source"] for d in cleaned)
    sectors = Counter(d["sector"] for d in cleaned)
    types   = Counter(d["investor_type"] for d in cleaned)
    scores  = [d["completeness_score"] for d in cleaned]
    states  = Counter(d["hq_state"] for d in cleaned if d["hq_state"])

    fields = [
        "name", "website", "hq_state", "aum_usd", "description",
        "sector", "stage", "focus", "key_people", "portfolio",
        "contacts", "founded_year", "aum_display",
    ]

    print(f"\n{sep}")
    print("  merge_final.py — 清洗报告")
    print(sep)
    print(f"  原始记录:     {raw_count:>8,}")
    print(f"  删除（无名）: {removed_nulls:>8,}")
    print(f"  删除（重复）: {removed_dups:>8,}")
    print(f"  最终记录:     {total:>8,}")
    print(f"  行业聚焦:     {len(focused):>8,}  ({100*len(focused)//total}%)")

    print(f"\n{'─'*40}")
    print("【数据来源】")
    for s, c in sources.most_common():
        pct = 100 * c // total
        print(f"  {str(s):25} {c:6,}  ({pct:2d}%)")

    print(f"\n{'─'*40}")
    print("【行业分布】")
    for s, c in sectors.most_common():
        bar = "█" * (c // 50)
        print(f"  {str(s):15} {c:6,}  {bar}")

    print(f"\n{'─'*40}")
    print("【投资人类型】")
    for t, c in types.most_common():
        print(f"  {str(t):25} {c:6,}")

    print(f"\n{'─'*40}")
    print("【字段完整度】")
    for f in fields:
        filled = sum(1 for d in cleaned if d.get(f) and d[f] not in ([], {}, "", None))
        pct = 100 * filled // total
        bar = "█" * (pct // 5)
        print(f"  {f:22} {filled:6,} / {total:,}  ({pct:3d}%)  {bar}")

    print(f"\n{'─'*40}")
    print("【完整度评分分布（0-100）】")
    buckets = Counter((s // 10) * 10 for s in scores)
    for low in sorted(buckets):
        high = low + 9
        print(f"  {low:3d}–{high:3d}  {buckets[low]:6,}  {'█' * (buckets[low] // 100)}")
    avg = sum(scores) // len(scores)
    print(f"  平均分: {avg}")

    print(f"\n{'─'*40}")
    print("【州分布（前10）】")
    for st, c in states.most_common(10):
        print(f"  {st:4}  {c:6,}")

    # Highlight new fields
    has_slug    = sum(1 for d in cleaned if d.get("slug"))
    has_aum_disp = sum(1 for d in cleaned if d.get("aum_display"))
    has_check   = sum(1 for d in cleaned if d.get("check_size_display"))
    print(f"\n{'─'*40}")
    print("【新增字段覆盖】")
    print(f"  slug:               {has_slug:6,}  ({100*has_slug//total}%)")
    print(f"  aum_display:        {has_aum_disp:6,}  ({100*has_aum_disp//total}%)")
    print(f"  check_size_display: {has_check:6,}  ({100*has_check//total}%)")
    print(f"  completeness_score: {total:6,}  (100%)")

    print(f"\n{sep}")
    print("输出文件:")
    print(f"  data/investors_final.json  ({total:,} 条)")
    print(f"  web/investors.json         ({total:,} 条，已覆盖)")
    print(sep)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    print(f"读取 {SRC} ...")
    with open(SRC, encoding="utf-8") as f:
        raw_data = json.load(f)
    raw_count = len(raw_data)
    print(f"原始记录: {raw_count:,}")

    # Clean
    print("清洗中...")
    cleaned_or_none = [clean_record(r) for r in raw_data]
    removed_nulls = sum(1 for r in cleaned_or_none if r is None)
    cleaned = [r for r in cleaned_or_none if r is not None]
    print(f"  删除无效记录: {removed_nulls}")

    # Dedup
    print("去重中...")
    cleaned, removed_dups = dedup(cleaned)
    print(f"  删除重复记录: {removed_dups}")
    print(f"  最终记录: {len(cleaned):,}")

    # Sort: sector-focused first, then by completeness_score desc, then name
    cleaned.sort(key=lambda d: (
        0 if d["sector"] != "general" else 1,
        -d["completeness_score"],
        d["name"],
    ))

    # Write outputs
    os.makedirs(os.path.dirname(DEST), exist_ok=True)

    print(f"写入 {DEST} ...")
    with open(DEST, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)

    print(f"写入 {WEB} ...")
    with open(WEB, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)

    print_report(raw_count, cleaned, removed_nulls, removed_dups)


if __name__ == "__main__":
    main()
