"""
人工最终修正脚本
Usage: python scraper/manual_final_correction.py
"""

import json
import re
from collections import Counter
from difflib import SequenceMatcher

# ── 第一类：从备份找回被误删的真实 VC ────────────────────────────────────
RESTORE_NAMES = [
    "Sabertooth Capital",
    "Walden Catalyst",
    "Scale Venture Partners",
    "Spark Capital",
    "Celesta Capital",
    "Digital Currency Group",
    "Silent Ventures",
    "Humba Ventures",
    "Cybernetix Ventures",
    "Cantos Ventures",
    "Nomad Fund Ventures",
    "Pruven Capital",
    "Albatross AI Capital",
    "Rethink Education",
    "LearnLaunch Accelerator",
    "Emerge Education",
    "SV Health Investors",
    "General Atlantic",
]

# ── 第二类：从 investors.json 删除脏数据 ─────────────────────────────────
DELETE_NAMES = {
    "Andy Strunk", "Eric Schmidt", "Danai Sakutukwa", "David Sokolic",
    "Sean Tillery", "Sherry Xie", "Mystery Science", "Applied Intuition",
    "Alloy Automation", "Hippocratic AI", "Magic Eden", "Astro Mechanica",
    "Adaptive Security", "Aria Pharmaceuticals", "Air Space Intelligence",
    "Clover Finance", "Akita Software", "Thinking Machines Lab",
    "Arpeggi Labs", "Barefoot Networks", "Digital Ocean", "FTX",
    "Climate Coverage", "Munich Re Ventures", "Temasek Holdings",
    "IDG Capital", "Boston Schwarz", "KPN Ventures",
    # 名称问题导致的无效记录
    "AI", "India", "Private equity", "2 Ventures managing partner",
    "is a non-traditional venture fund",
}

# ── 第三类：修正名称错误（None 表示删除）────────────────────────────────
NAME_FIXES = {
    "Aaron Holiday645 Ventures":                   "645 Ventures",
    "Chris Kaster":                                "Cadence MedTech Ventures",
    "StrabalaTrue Wealth Ventures":                "True Wealth Ventures",
    "Ally WarsonUP.Partners":                      "UP.Partners",
    "KlingerRemote First Capital":                 "Remote First Capital",
    "BesvinickLooking Glass Capital":              "Looking Glass Capital",
    "ShapiroManaging Partner, Julian Capital":     "Julian Capital",
    "Foundation, CEVG, Holcim MAQER Ventures":    "Holcim MAQER Ventures",
    "Renewables Group renewable energy industry":  "US Renewables Group",
    "Investment Partners clean energy":            "Oak Investment Partners",
    "Ventures midstream geological gas storage":   "Haddington Ventures",
    "GROWTH AREAS (BGA) (Remaining)":              None,
    "Fintech Capital Map":                         None,
    "Deep Tech Capital Map":                       None,
    "AI Capital Map":                              None,
}

# ── 模糊匹配阈值 ──────────────────────────────────────────────────────────
FUZZY_THRESHOLD = 0.82


def fuzzy_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def name_key(name: str) -> str:
    """归一化名称用于重复判断"""
    n = re.sub(
        r'\b(llc|lp|inc|ltd|co|corp|fund|capital|ventures?|partners?|'
        r'group|management|investments?)\b', '', name.lower()
    )
    return re.sub(r'[^a-z0-9]', '', n)


def calc_completeness(d: dict) -> int:
    weights = {
        "name": 10, "website": 10, "hq_state": 8, "investor_type": 8,
        "sector": 8, "aum_usd": 10, "description": 8, "key_people": 8,
        "portfolio": 8, "contacts": 10, "founded_year": 5, "focus": 7,
    }
    score = 0
    for field, w in weights.items():
        val = d.get(field)
        if val and val not in ([], {}, "", None):
            score += w
    return score


# ══════════════════════════════════════════════════════════════════════════
def main():
    # ── 加载数据 ──────────────────────────────────────────────────────────
    with open("web/investors.json", encoding="utf-8") as f:
        records = json.load(f)

    with open("data/exa_before_deep_clean.json", encoding="utf-8") as f:
        backup = json.load(f)

    print(f"{'='*60}")
    print(f"  人工最终修正脚本")
    print(f"{'='*60}")
    print(f"  载入记录:  {len(records):,}")
    print(f"  备份记录:  {len(backup):,}")

    # ── 第二类：删除脏数据 ────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("第二类：删除脏数据")

    deleted = []
    kept    = []
    for r in records:
        name = r.get("name", "")
        if name in DELETE_NAMES:
            deleted.append(name)
        else:
            kept.append(r)

    not_found_del = [n for n in DELETE_NAMES if n not in {r.get("name") for r in records}]
    print(f"  删除: {len(deleted)} 条")
    for n in deleted:
        print(f"    ❌ {n}")
    if not_found_del:
        print(f"  未找到（已不在库中）: {len(not_found_del)} 条")
        for n in not_found_del:
            print(f"    ⚠️  {n}")

    records = kept

    # ── 第三类：修正名称错误 ──────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("第三类：修正名称错误")

    rename_done = []
    delete_via_fix = []
    fix_not_found  = []

    for old_name, new_name in NAME_FIXES.items():
        found = False
        for r in records:
            if r.get("name") == old_name:
                found = True
                if new_name is None:
                    delete_via_fix.append(old_name)
                else:
                    r["name"] = new_name
                    rename_done.append((old_name, new_name))
                break
        if not found:
            fix_not_found.append(old_name)

    # 删除 None 修正的记录
    delete_via_fix_set = {k for k, v in NAME_FIXES.items() if v is None}
    records = [r for r in records if r.get("name") not in delete_via_fix_set]

    print(f"  重命名: {len(rename_done)} 条")
    for old, new in rename_done:
        print(f"    ✏️  '{old}' → '{new}'")
    if delete_via_fix:
        print(f"  删除（None修正）: {len(delete_via_fix)} 条")
        for n in delete_via_fix:
            print(f"    ❌ {n}")
    if fix_not_found:
        print(f"  未找到（已不在库中）: {len(fix_not_found)} 条")
        for n in fix_not_found:
            print(f"    ⚠️  {n}")

    # ── 第一类：从备份找回误删 VC ─────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("第一类：从备份找回被误删的真实 VC")

    existing_names     = {r.get("name", "") for r in records}
    existing_keys      = {name_key(n) for n in existing_names}
    backup_by_name     = {r.get("name", ""): r for r in backup}

    restored      = []
    already_exist = []
    not_found_res = []

    for target in RESTORE_NAMES:
        # 检查是否已在库中（精确 or 归一化 key）
        if target in existing_names:
            already_exist.append(target)
            continue
        if name_key(target) in existing_keys:
            already_exist.append(f"{target}（同义名已存在）")
            continue

        # 精确匹配备份
        rec = backup_by_name.get(target)

        # 模糊匹配备份
        if rec is None:
            best_ratio, best_rec = 0.0, None
            for bname, brec in backup_by_name.items():
                r = fuzzy_ratio(target, bname)
                if r > best_ratio:
                    best_ratio, best_rec = r, brec
            if best_rec and best_ratio >= FUZZY_THRESHOLD:
                rec = best_rec
                print(f"    🔍 模糊匹配 '{target}' → '{best_rec.get('name')}' ({best_ratio:.2f})")

        if rec:
            # 确保名称是目标名称（模糊匹配时修正）
            restored_rec = {**rec, "name": target}
            restored_rec["completeness_score"] = calc_completeness(restored_rec)
            restored.append(restored_rec)
            existing_names.add(target)
            existing_keys.add(name_key(target))
        else:
            not_found_res.append(target)

    records = records + restored

    print(f"  找回: {len(restored)} 条")
    for r in restored:
        print(f"    ✅ {r['name']}")
    if already_exist:
        print(f"  已在库中，跳过: {len(already_exist)} 条")
        for n in already_exist:
            print(f"    ⚪ {n}")
    if not_found_res:
        print(f"  备份中未找到: {len(not_found_res)} 条")
        for n in not_found_res:
            print(f"    ❓ {n}")

    # ── 重算 completeness_score ───────────────────────────────────────────
    for r in records:
        r["completeness_score"] = calc_completeness(r)

    # ── 排序 ──────────────────────────────────────────────────────────────
    EXA_SOURCES = {
        "exa_directory", "exa_vc_directories",
        "authority_list", "pdf_report", "exa_direct",
    }
    records.sort(key=lambda x: (
        0 if x.get("sector") not in (None, "", "general") else 1,
        -(x.get("completeness_score") or 0),
        x.get("name", ""),
    ))

    # ── 写出 ──────────────────────────────────────────────────────────────
    with open("web/investors.json", "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    with open("data/investors_final.json", "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    # ── 最终统计 ──────────────────────────────────────────────────────────
    exa_records = [r for r in records if r.get("data_source", "") in EXA_SOURCES]
    sec_records  = [r for r in records if r.get("data_source", "") not in EXA_SOURCES]
    sectors      = Counter(r.get("sector") for r in exa_records)
    scores       = [r.get("completeness_score", 0) for r in records]

    sector_lines = "".join(
        f"  {(s or '?'):15s} {c}\n" for s, c in sectors.most_common()
    )

    print(f"""
{'='*60}
✅ 人工最终修正完成
{'='*60}
操作汇总:
  第一类 找回:     +{len(restored):,} 条
  第二类 删除:     -{len(deleted) + len(delete_via_fix):,} 条
  第三类 重命名:   {len(rename_done):,} 条

最终总记录:        {len(records):,}
  SEC 来源:        {len(sec_records):,}
  Exa 来源:        {len(exa_records):,}

Exa 行业分布:
{sector_lines}
完整度评分（全库）:
  平均分:   {sum(scores)/len(scores):.1f}
  80+ 分:   {sum(1 for s in scores if s >= 80)}
  50-79 分: {sum(1 for s in scores if 50 <= s < 80)}
  50 以下:  {sum(1 for s in scores if s < 50)}

输出:
  web/investors.json        ✅
  data/investors_final.json ✅
{'='*60}
""")


if __name__ == "__main__":
    main()
