#!/usr/bin/env python3
"""Convert enriched.jsonl → web/investors.json for the frontend."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

SRC  = ROOT / "data" / "enrichment_target" / "enriched.jsonl"
DEST = ROOT / "web" / "investors.json"

GEO_TO_STATE = {
    "sf_bay_area": "CA",
    "nyc":         "NY",
    "boston":      "MA",
    "la":          "CA",
    "seattle":     "WA",
    "austin":      "TX",
    "chicago":     "IL",
    "miami":       "FL",
}

CONFIDENCE_TO_SOURCE = {
    "high":   "verified",
    "medium": "partial",
    "low":    "unverified",
}


def convert(r: dict) -> dict:
    geo = r.get("geo_focus") or []
    hq_state = GEO_TO_STATE.get(geo[0]) if geo else None

    sectors = r.get("sectors") or []
    sector  = sectors[0] if sectors else None

    thesis = r.get("thesis_text") or ""
    description = thesis[:300] if thesis else None

    raw_people = r.get("partner_contacts") or []
    key_people = [
        {"name": p.get("name"), "title": p.get("title")}
        for p in raw_people
        if p.get("name")
    ]

    raw_portfolio = r.get("portfolio_companies") or []
    portfolio = [
        p["name"] for p in raw_portfolio
        if isinstance(p, dict) and p.get("name")
    ]

    return {
        "name":                  r.get("display_name") or r.get("name"),
        "website":               r.get("official_website"),
        "type":                  r.get("investor_type"),
        "hq_state":              hq_state,
        "data_source":           CONFIDENCE_TO_SOURCE.get(r.get("overall_confidence"), "unverified"),

        "stages":                r.get("stages"),
        "sectors":               sectors,
        "sector":                sector,
        "geo_focus":             r.get("geo_focus") or [],
        "lead_investor":         r.get("lead_investor"),
        "check_size":            r.get("check_size_display"),
        "check_size_min_usd":    r.get("check_size_min_usd"),
        "check_size_max_usd":    r.get("check_size_max_usd"),

        "description":           description,
        "key_people":            key_people or None,
        "portfolio":             portfolio or None,

        "completeness_score":    r.get("tier1_completeness", 0),
        "tier2_completeness":    r.get("tier2_completeness", 0),
        "contacts_completeness": r.get("contacts_completeness", 0),

        "submission_url":        r.get("submission_url"),
        "general_email":         r.get("general_email"),
    }


def main():
    records = []
    with open(SRC) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    total_in = len(records)

    # Skip null display_name
    records = [r for r in records if r.get("display_name") is not None]
    skipped = total_in - len(records)

    # Deduplicate by name: keep highest tier1_completeness
    best: dict[str, dict] = {}
    for r in records:
        name = r.get("display_name") or r.get("name") or ""
        if name not in best or r.get("tier1_completeness", 0) > best[name].get("tier1_completeness", 0):
            best[name] = r

    deduped   = len(records) - len(best)
    out_list  = [convert(r) for r in best.values()]

    DEST.parent.mkdir(parents=True, exist_ok=True)
    with open(DEST, "w") as f:
        json.dump(out_list, f, ensure_ascii=False, indent=2)

    size_kb = DEST.stat().st_size / 1024
    print(f"输入：{total_in} 条")
    print(f"跳过（display_name=null）：{skipped} 条")
    print(f"去重：{deduped} 条")
    print(f"输出：{len(out_list)} 条")
    print(f"文件大小：{size_kb:.1f} KB  →  {DEST}")


if __name__ == "__main__":
    main()
