"""
Manual correction pass on web/investors.json (Exa-sourced records).

Steps:
  1. Remove dirty records by exact name match
  2. Restore falsely-rejected VCs from backup
  3. Clean noisy name suffixes
  4. Final dedup by (clean_name, hq_state)
  5. Write web/investors.json + data/investors_final.json
"""

import json
import re
import os
from difflib import SequenceMatcher

# ── Lists ──────────────────────────────────────────────────────────────────

REMOVE_FROM_KEPT = {
    # numeric — investment counts, not firm names
    "106 investments", "117 investments", "237 investments",
    "84 investments", "93 investments",
    # person names
    "Andy Strunk", "Danai Sakutukwa", "David Sokolic",
    "Eric Schmidt", "Sean Tillery", "Sherry Xie",
    "Aaron MichelPartner, 1984 Ventures",
    "Abbie StrabalaTrue Wealth Ventures",
    "Adam BesvinickLooking Glass Capital",
    "Alejandra MartinezRoom40 Ventures",
    "Andrew McLoughlinUncork Capital",
    "Amitt MahajanPresence Capital",
    "Brook Bisrat, Partner",
    "Chris Kaster - Cadence MedTech Ventures",
    "General Partner, Earthshot Ventures",
    "Greylock·Venture Partner",
    "Kuhling | ONSET Ventures",
    "Cantos Ventures·General Partner",
    # descriptive / generic
    "AI - Secondaries", "Airspace management systems",
    "Autonomy & Robotics", "Climate Coverage",
    "Companies Funded", "Direct Investing Fund",
    "Distributed Energy Management",
    "Equity+ Structured Credit",
    "Export AI Capital Map", "Export Deep Tech Capital Map",
    "Fund I", "Investor Calendar 2026",
    "Investor Performance", "Knowledge Management",
    "LP Perspectives", "Median Round Size",
    "No Barrier AI", "Nucleation is a non-traditional venture fund",
    "Private equity", "Privacy Center",
    "Select Fund", "Senior Advisor",
    "Venture Capitalist", "Working Capital Fund",
    "Why is Thrive Capital ranked #1?+",
    "Scroll to experience 5AM Ventures",
    # duplicates / variants
    "AXA Venture Partners1 shared",
    "Dell Technologies Capital 2 shared",
    "Boehringer Ingelheim Venture Fund1 shared",
    "Founders Fund 33",
    "Initialized Capital 45 shared",
    "Mayfield Fund 2 shared",
    "NF Ventures 1 shared",
    "Robert Bosch Venture Capital4 shared",
    "Nucleation Capital : FUND I",
    "Google Ventures (GV)",
    # NOTE: Insight Partners / Intel Capital handled by dedup — do NOT add here
    # portfolio companies / startups
    "Akita Software", "Alloy Automation", "Applied Intuition",
    "Aria Pharmaceuticals", "Astro Mechanica",
    "Barefoot Networks", "Clover Finance",
    "FTX - Coinbase", "Hippocratic AI",
    "Magic Eden", "Thinking Machines Lab",
    "Artillery Games",
    # other noise
    "BUSINESS GROWTH AREAS (BGA) (Remaining)",
    "NVCA Member Spotlight: .406 Ventures",
    "NVCA Member Spotlight: Nationwide Ventures",
    "NVCA Member Spotlight: Sapphire Ventures",
    "NVCA Member Spotlight: Shadow Ventures",
    "Honey Badger Capital Management --",
    "Grantham Foundation, CEVG, Holcim MAQER Ventures",
    "Energy Impact Partners, MassMutual Ventures",
    "Boston Schwarz",
    "FJ Labs",
}

RESTORE_FROM_REJECTED = [
    "Prime Movers Lab",
    "Sabertooth Capital",
    "Digital Currency Group",
    "Scale Venture Partners",
    "Spark Capital",
    "Walden Catalyst",
    "Celesta Capital",
    "Draper Associates",
    "Unshackled Ventures",
    "Better Ventures",
    "Ally Ventures",
    "Alexa Fund",
    "Wildcat Venture Partners",
    "SV Health Investors",
    "Qbeat Ventures",
    "Blue Elephant Capital",
    "Arizona Nuclear Ventures",
    "Beyond Earth Ventures",
    "Drone.VC",
    "Alley Robotics Ventures",
    "AccelR8",
    "Advanced Tech Fund",
    "Coalition Ventures",
    "Cortex Industrial Partners",
    "Dedicated Fund Capital",
    "AMD Ventures",
]

EXA_SOURCES = {
    "exa_directory", "exa_vc_directories",
    "authority_list", "pdf_report", "exa_direct",
}

# ── Name cleaning ──────────────────────────────────────────────────────────

_SHARED_RE   = re.compile(r'\s+\d+\s+shared$', re.IGNORECASE)
_TITLE_SUFFIXES = [
    "·Managing Director", "·Venture Partner", "·General Partner",
    "·Partner", " clean energy",
    # NOTE: do NOT include bare " energy" — many firms have Energy in their name
]

def clean_name(name: str) -> str:
    name = _SHARED_RE.sub("", name).strip()
    for suf in _TITLE_SUFFIXES:
        if name.lower().endswith(suf.lower()):
            name = name[: -len(suf)].strip()
    # Only strip trailing " energy" when preceded by an adjective/descriptor word
    # e.g. "True North Venture Partners energy" but NOT "Form Energy"
    if re.search(r'\b(partners?|ventures?|capital|fund|management)\s+energy$', name, re.I):
        name = re.sub(r'\s+energy$', '', name, flags=re.I).strip()
    return name


# ── Dedup ──────────────────────────────────────────────────────────────────

def norm_key(name: str, state) -> str:
    n = re.sub(r'\b(llc|lp|inc|ltd|co|corp|fund|capital|ventures?|partners?|group|management|investments?)\b',
               '', name.lower())
    n = re.sub(r'[^a-z0-9]', '', n)
    return f"{n}|{(state or '').upper()}"


def dedup(records: list[dict]) -> tuple[list[dict], int]:
    seen: dict[str, dict] = {}
    for r in records:
        key = norm_key(clean_name(r.get("name", "")), r.get("hq_state"))
        if key not in seen:
            seen[key] = r
        else:
            # keep whichever has higher completeness_score
            if r.get("completeness_score", 0) > seen[key].get("completeness_score", 0):
                seen[key] = r
    dupes = len(records) - len(seen)
    return list(seen.values()), dupes


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    # Load current data
    with open("web/investors.json") as f:
        all_data = json.load(f)

    exa_records = [d for d in all_data if d.get("data_source", "") in EXA_SOURCES]
    sec_records  = [d for d in all_data if d.get("data_source", "") not in EXA_SOURCES]

    print(f"载入数据: {len(all_data):,} 条  (SEC={len(sec_records):,}  Exa={len(exa_records):,})")

    # Load backup for restore step
    with open("data/investors_before_gemini_clean.json") as f:
        backup_all = json.load(f)
    backup_exa = {r.get("name", ""): r for r in backup_all if r.get("data_source", "") in EXA_SOURCES}

    # ── STEP 1: Remove dirty records ──────────────────────────────────────
    before_remove = len(exa_records)
    removed_names = []
    exa_clean = []
    for r in exa_records:
        if r.get("name", "") in REMOVE_FROM_KEPT:
            removed_names.append(r["name"])
        else:
            exa_clean.append(r)

    not_found_remove = [n for n in REMOVE_FROM_KEPT if n not in {r["name"] for r in exa_records}]
    print(f"\n▶ 步骤1 — 删除脏数据")
    print(f"  删除: {len(removed_names)} 条  (未找到: {len(not_found_remove)} 条)")
    for n in removed_names:
        print(f"    ❌ {n}")

    # ── STEP 2: Restore falsely-rejected VCs ──────────────────────────────
    existing_names = {r.get("name", "") for r in exa_clean}
    restored = []
    not_found_restore = []
    for name in RESTORE_FROM_REJECTED:
        if name in existing_names:
            print(f"  ⚠️  已存在，跳过: {name}")
            continue
        # exact match first
        rec = backup_exa.get(name)
        if rec is None:
            # fuzzy match
            best, best_ratio = None, 0.0
            for bname, brec in backup_exa.items():
                ratio = SequenceMatcher(None, name.lower(), bname.lower()).ratio()
                if ratio > best_ratio:
                    best_ratio, best = ratio, brec
            if best and best_ratio >= 0.85:
                rec = best
        if rec:
            restored.append(rec)
            existing_names.add(rec.get("name", ""))
        else:
            not_found_restore.append(name)

    print(f"\n▶ 步骤2 — 找回误删 VC")
    print(f"  找回: {len(restored)} 条  (未找到: {len(not_found_restore)} 条)")
    for r in restored:
        print(f"    ✅ {r['name']}")
    if not_found_restore:
        for n in not_found_restore:
            print(f"    ⚠️  备份中未找到: {n}")

    exa_clean = exa_clean + restored

    # ── STEP 3: Clean name suffixes ───────────────────────────────────────
    cleaned_names = []
    for r in exa_clean:
        orig = r.get("name", "")
        new  = clean_name(orig)
        if new != orig:
            cleaned_names.append((orig, new))
            r["name"] = new

    print(f"\n▶ 步骤3 — 清洗名称后缀")
    print(f"  清洗: {len(cleaned_names)} 条")
    for orig, new in cleaned_names:
        print(f"    '{orig}'  →  '{new}'")

    # ── STEP 4: Dedup ─────────────────────────────────────────────────────
    exa_deduped, dupe_count = dedup(exa_clean)
    print(f"\n▶ 步骤4 — 去重")
    print(f"  去除重复: {dupe_count} 条")
    print(f"  去重后 Exa 记录: {len(exa_deduped)}")

    # ── Final write ───────────────────────────────────────────────────────
    final = sec_records + exa_deduped
    final.sort(key=lambda x: (
        0 if x.get("sector") not in (None, "", "general") else 1,
        -(x.get("completeness_score") or 0),
        x.get("name", ""),
    ))

    with open("web/investors.json", "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)

    with open("data/investors_final.json", "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)

    exa_after = [d for d in final if d.get("data_source", "") in EXA_SOURCES]

    print(f"""
{'='*60}
✅ 人工修正完成
{'='*60}
Exa 修正前:   {before_remove:,}
  Step1 删除: -{len(removed_names):,}
  Step2 找回: +{len(restored):,}
  Step4 去重: -{dupe_count:,}
Exa 修正后:   {len(exa_deduped):,}

SEC 记录:     {len(sec_records):,}
最终总记录:   {len(final):,}

写入文件:
  web/investors.json
  data/investors_final.json
{'='*60}
""")

    # Per-sector breakdown
    from collections import Counter
    sectors = Counter(d.get("sector", "?") for d in exa_after)
    print("Exa 记录按行业分布:")
    for s, c in sectors.most_common():
        print(f"  {s:15} {c:4}")


if __name__ == "__main__":
    main()
