#!/usr/bin/env python3
"""Backfill display_name for existing enriched.jsonl records."""

import json
import sys
from pathlib import Path

# Allow running as script from any working directory
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from enrichment.merger import clean_display_name

TARGET = ROOT / "data" / "enrichment_target" / "enriched.jsonl"
TMP    = TARGET.with_suffix(".jsonl.tmp")


def main():
    records = []
    with open(TARGET) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    null_count = 0
    for r in records:
        dn = clean_display_name(r.get("name"), r.get("official_website"))
        r["display_name"] = dn
        if dn is None:
            null_count += 1

    with open(TMP, "w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    TMP.replace(TARGET)

    print(f"处理了 {len(records)} 条记录")
    print(f"display_name 为 null：{null_count} 条")
    print(f"display_name 有值：{len(records) - null_count} 条")


if __name__ == "__main__":
    main()
