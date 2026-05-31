#!/usr/bin/env python3
"""
Enrichment pipeline CLI — v2.

Usage:
  python -m enrichment.main --limit 20 --fresh-start
  python -m enrichment.main --limit 1000 --concurrency 3
  python -m enrichment.main --resume
  python -m enrichment.main --report-only
"""

import argparse
import asyncio
import json
import os
import random
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

import aiohttp
import anthropic

from .state import ensure_dirs, load_completed, mark_completed, append_jsonl, get_paths
from .processor import enrich_one
from .merger import merge

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(it, **kwargs):
        return it


DEFAULT_DATA_FILE = Path(__file__).parent.parent / "data" / "investors_final.json"
DEFAULT_OUTDIR    = Path(__file__).parent.parent / "data" / "enrichment_v2"


def load_investors(limit: int, resume: bool, output_dir: Path, data_file: Path) -> list[dict]:
    with open(data_file) as f:
        investors = json.load(f)
    if resume:
        completed = load_completed(output_dir)
        investors = [r for r in investors if r.get("id") not in completed]
        print(f"Resume: {len(completed)} done, {len(investors)} remaining")
    if limit:
        investors = investors[:limit]
    return investors


def _enrich_sync(record: dict, api_key: str) -> dict:
    """Run enrich_one in its own event loop (called from a thread)."""
    async def _run():
        client = anthropic.AsyncAnthropic(api_key=api_key, timeout=120.0)
        try:
            async with aiohttp.ClientSession() as session:
                result = await enrich_one(record, client, session)
        finally:
            # Suppress cleanup errors — they must not override the real result
            try:
                await client.aclose()
            except Exception:
                pass
        return result

    return asyncio.run(_run())


def run(args):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    output_dir: Path = args.output_dir

    if args.fresh_start and output_dir.exists():
        shutil.rmtree(output_dir)
        print(f"Cleared {output_dir}")

    ensure_dirs(output_dir)
    paths = get_paths(output_dir)

    investors = load_investors(args.limit, args.resume, output_dir, args.input_file)
    if not investors:
        print("Nothing to process.")
        return

    print(f"Processing {len(investors)} investors  (concurrency={args.concurrency}, output={output_dir})")

    write_lock = threading.Lock()

    stats = {
        "enriched": 0, "rejected": 0, "failed": 0,
        "total_tokens_in": 0, "total_tokens_out": 0, "total_elapsed": 0.0,
        "rejections": [],
        "failures": [],
        "enriched_records": [],
    }
    stats_lock = threading.Lock()

    def process_one(record: dict):
        result = _enrich_sync(record, api_key)

        rid = record.get("id", record.get("name", "?"))

        with stats_lock:
            stats["total_tokens_in"]  += result.get("tokens_input", 0)
            stats["total_tokens_out"] += result.get("tokens_output", 0)
            stats["total_elapsed"]    += result.get("elapsed_s", 0)

        if result["status"] == "enriched":
            merged = merge(record, result["enriched"])
            with write_lock:
                append_jsonl(paths["enriched"], merged)
                mark_completed(output_dir, rid)
            with stats_lock:
                stats["enriched"] += 1
                stats["enriched_records"].append(merged)

        elif result["status"] == "rejected":
            with write_lock:
                append_jsonl(paths["rejected"], {
                    "id": rid, "name": record.get("name"),
                    "reason": result.get("rejection_reason"),
                })
                mark_completed(output_dir, rid)
            with stats_lock:
                stats["rejected"] += 1
                stats["rejections"].append((record.get("name"), result.get("rejection_reason")))

        else:
            with write_lock:
                append_jsonl(paths["failed"], {
                    "id": rid, "name": record.get("name"),
                    "error": result.get("error"),
                })
            with stats_lock:
                stats["failed"] += 1
                stats["failures"].append((record.get("name"), result.get("error")))

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {executor.submit(process_one, r): r for r in investors}
        for fut in tqdm(as_completed(futures), total=len(futures), desc="Enriching"):
            try:
                fut.result()
            except Exception as e:
                rec = futures[fut]
                print(f"\n[WORKER ERROR] {rec.get('name','?')}: {e}")

    print_report(investors, stats)


# ── Report ────────────────────────────────────────────────────────────

def print_report(investors: list, stats: dict):
    n        = len(investors)
    enriched = stats["enriched"]
    rejected = stats["rejected"]
    failed   = stats["failed"]
    ti       = stats["total_tokens_in"]
    to_      = stats["total_tokens_out"]
    avg_el   = stats["total_elapsed"] / n if n else 0
    avg_ti   = ti / n if n else 0
    avg_to   = to_ / n if n else 0

    records = stats["enriched_records"]

    t1_coverage = {
        "stages":  sum(1 for r in records if r.get("stages")),
        "sectors": sum(1 for r in records if r.get("sectors")),
        "check_size": sum(1 for r in records if r.get("check_size_min_usd") or r.get("check_size_max_usd")),
        "geo_focus": sum(1 for r in records if r.get("geo_focus")),
    }
    t2_coverage = {
        "thesis_text":               sum(1 for r in records if r.get("thesis_text")),
        "business_model_preference": sum(1 for r in records if r.get("business_model_preference")),
        "traction_requirement":      sum(1 for r in records if r.get("traction_requirement")),
        "lead_investor":             sum(1 for r in records if r.get("lead_investor")),
    }
    t1_dist = {100: 0, 75: 0, 50: 0, 25: 0, 0: 0}
    conf_dist = {"high": 0, "medium": 0, "low": 0}
    for r in records:
        score = r.get("tier1_completeness", 0)
        bucket = max(k for k in t1_dist if k <= score)
        t1_dist[bucket] += 1
        conf = r.get("overall_confidence", "low")
        conf_dist[conf] = conf_dist.get(conf, 0) + 1

    def pct(x): return f"{x/enriched*100:.1f}%" if enriched else "N/A"

    sep = "=" * 65
    print(f"\n{sep}")
    print(f"  ENRICHMENT V2 REPORT — {n} records")
    print(sep)

    print(f"\n1. 处理结果")
    print(f"   Enriched : {enriched}  ({enriched/n*100:.1f}%)")
    print(f"   Rejected : {rejected}  ({rejected/n*100:.1f}%)")
    print(f"   Failed   : {failed}   ({failed/n*100:.1f}%)")

    print(f"\n2. 非投资机构识别 ({rejected})")
    if stats["rejections"]:
        for name, reason in stats["rejections"]:
            short = (reason or "")[:120]
            print(f"   - {name}: {short}")
    else:
        print("   (none)")

    print(f"\n3. Tier 1 字段覆盖率")
    for field, count in t1_coverage.items():
        print(f"   {field:<25} {count}/{enriched} ({pct(count)})")

    print(f"\n4. Tier 2 字段覆盖率")
    for field, count in t2_coverage.items():
        print(f"   {field:<25} {count}/{enriched} ({pct(count)})")

    print(f"\n5. tier1_completeness 分布")
    for score in [100, 75, 50, 25, 0]:
        label = f"{score}分"
        print(f"   {label:<8} {t1_dist[score]} 条")

    print(f"\n6. overall_confidence 分布")
    for level in ["high", "medium", "low"]:
        print(f"   {level:<8} {conf_dist.get(level, 0)} 条")

    print(f"\n7. thesis_text 样本 (随机3条)")
    thesis_records = [r for r in records if r.get("thesis_text")]
    samples = random.sample(thesis_records, min(3, len(thesis_records)))
    for i, r in enumerate(samples, 1):
        thesis = (r["thesis_text"] or "")[:600]
        if len(r.get("thesis_text") or "") > 600:
            thesis += "..."
        print(f"\n   [{i}] {r['name']} (T1={r['tier1_completeness']}, {r['overall_confidence']})")
        print(f"   {'─'*55}")
        for line in thesis.split("\n"):
            print(f"   {line}")

    sub_found     = sum(1 for r in records if r.get("submission_url"))
    email_found   = sum(1 for r in records if r.get("general_email"))
    partners_all  = [p for r in records for p in (r.get("partner_contacts") or [])]
    li_found_recs = sum(1 for r in records if any(p.get("linkedin_url") for p in (r.get("partner_contacts") or [])))
    li_total      = sum(1 for p in partners_all if p.get("linkedin_url"))
    avg_li        = li_total / enriched if enriched else 0

    print(f"\n8. Contacts 字段覆盖率")
    print(f"   submission_url        {sub_found}/{enriched} ({pct(sub_found)})")
    print(f"   general_email         {email_found}/{enriched} ({pct(email_found)})")
    print(f"   firms with ≥1 LinkedIn {li_found_recs}/{enriched} ({pct(li_found_recs)})")
    print(f"   avg LinkedIn/firm     {avg_li:.1f}  (total {li_total} URLs)")

    print(f"\n9. Contacts 样本 (随机3条)")
    contact_records = [r for r in records if r.get("partner_contacts") or r.get("submission_url")]
    csamples = random.sample(contact_records, min(3, len(contact_records)))
    for i, r in enumerate(csamples, 1):
        print(f"\n   [{i}] {r['name']}  (contacts_completeness={r.get('contacts_completeness', 0)})")
        print(f"   {'─'*55}")
        if r.get("submission_url"):
            print(f"   submission_url : {r['submission_url']}")
        if r.get("general_email"):
            print(f"   general_email  : {r['general_email']}")
        for p in (r.get("partner_contacts") or []):
            li  = p.get("linkedin_url") or "—"
            em  = p.get("email") or "—"
            tw  = p.get("twitter_x") or "—"
            print(f"   partner: {p.get('name')} ({p.get('title')})")
            print(f"     LinkedIn : {li}")
            print(f"     email    : {em}")
            print(f"     twitter  : {tw}")

    print(f"\n10. 性能")
    print(f"   Avg elapsed / record : {avg_el:.1f}s")
    print(f"   Avg input tokens     : {avg_ti:.0f}")
    print(f"   Avg output tokens    : {avg_to:.0f}")
    print(f"   Total tokens         : {ti + to_:,}")

    if stats["failures"]:
        print(f"\n11. 失败记录")
        for name, error in stats["failures"]:
            print(f"   - {name}: {error}")

    print(f"\n{sep}")


def show_report_only(output_dir: Path):
    paths = get_paths(output_dir)
    if not paths["enriched"].exists():
        print(f"No enriched.jsonl in {output_dir}")
        return
    records = [json.loads(l) for l in open(paths["enriched"]) if l.strip()]
    rejected = sum(1 for _ in open(paths["rejected"])) if paths["rejected"].exists() else 0
    failed   = sum(1 for _ in open(paths["failed"]))   if paths["failed"].exists()   else 0
    stats = {
        "enriched": len(records), "rejected": rejected, "failed": failed,
        "total_tokens_in": 0, "total_tokens_out": 0, "total_elapsed": 0,
        "rejections": [], "failures": [], "enriched_records": records,
    }
    print_report(records, stats)


def main():
    parser = argparse.ArgumentParser(description="Investor enrichment pipeline v2")
    parser.add_argument("--limit",       type=int,  default=0)
    parser.add_argument("--concurrency", type=int,  default=2)
    parser.add_argument("--resume",      action="store_true")
    parser.add_argument("--fresh-start", action="store_true", dest="fresh_start",
                        help="Clear output dir and restart from first record")
    parser.add_argument("--output-dir",  type=Path, default=DEFAULT_OUTDIR, dest="output_dir")
    parser.add_argument("--input",       type=Path, default=DEFAULT_DATA_FILE, dest="input_file",
                        help="Input JSON file (default: investors_final.json)")
    parser.add_argument("--report-only", action="store_true", dest="report_only")
    args = parser.parse_args()

    if args.report_only:
        show_report_only(args.output_dir)
        return

    run(args)


if __name__ == "__main__":
    main()
