"""
Investor Sub-Tag Enrichment — scraper/m_enrich_subtags.py

One-time batch script that reads every investor's thesis_text + portfolio_companies,
calls Claude Haiku to generate 3–8 fine-grained sub_tags, and writes them back
into active.jsonl.

Usage:
    python scraper/m_enrich_subtags.py                  # enrich all missing
    python scraper/m_enrich_subtags.py --force          # re-enrich everything
    python scraper/m_enrich_subtags.py --dry-run        # preview without API call
    python scraper/m_enrich_subtags.py --sample 10      # test on first 10 only

Cost:  ~$0.25 for all 485 investors (Claude Haiku)
Time:  ~3–5 minutes
"""

import argparse
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────────

ROOT       = Path(__file__).parent.parent
JSONL_PATH = ROOT / "data" / "enrichment_target" / "active.jsonl"

_api_key = os.getenv("ANTHROPIC_API_KEY")
if not _api_key:
    raise SystemExit("ERROR: ANTHROPIC_API_KEY not set in .env")

client = anthropic.Anthropic(api_key=_api_key)
MODEL  = "claude-haiku-4-5-20251001"

BATCH_SIZE  = 20
RATE_DELAY  = 1.0
MAX_RETRIES = 3

# ── Taxonomy ───────────────────────────────────────────────────────────────
# Master vocabulary. Claude must only output tags from this list.
# Add new tags here as you encounter new verticals — the script is re-runnable.

TAXONOMY = {
    "biotech_healthcare": [
        "crispr", "gene_therapy", "car_t", "cell_therapy", "oncology",
        "immunotherapy", "rare_disease", "synthetic_biology", "platform_biotech",
        "genomics", "proteomics", "drug_discovery", "digital_health", "diagnostics",
        "medtech", "mental_health", "longevity", "microbiome", "regenerative_medicine",
        "clinical_ai", "neuroscience", "cardiovascular", "metabolic_disease",
        "infectious_disease", "womens_health", "ophthalmology", "musculoskeletal",
    ],
    "fintech": [
        "payments", "lending", "neobank", "wealthtech", "insurtech", "regtech",
        "crypto_web3", "defi", "b2b_payments", "embedded_finance", "financial_infra",
        "sme_finance", "real_estate_finance", "trade_finance", "open_banking",
        "accounting_tax", "fraud_risk",
    ],
    "cleantech_climate": [
        "solar", "wind", "energy_storage", "grid_infra", "carbon_capture",
        "sustainable_food", "agtech_climate", "ev_charging", "green_hydrogen",
        "circular_economy", "water_tech", "sustainable_materials", "carbon_markets",
        "building_efficiency", "nuclear", "ocean_tech",
    ],
    "ai_ml": [
        "foundation_models", "ai_agents", "computer_vision", "nlp", "generative_ai",
        "ai_chips_infra", "mlops", "synthetic_data", "robotics_ai",
        "autonomous_vehicles", "ai_drug_discovery", "ai_security", "ai_finance",
        "ai_legal", "ai_healthcare", "multimodal_ai", "edge_ai",
        "reinforcement_learning", "ai_for_science",
    ],
    "deeptech_hardware": [
        "semiconductors", "quantum_computing", "photonics", "advanced_materials",
        "space_tech", "drones", "industrial_robotics", "sensors", "rf_communications",
        "fusion", "advanced_manufacturing", "bioelectronics", "nanotechnology",
    ],
    "defense_security": [
        "autonomous_defense", "cybersecurity", "intelligence_tools",
        "satellite_comms", "counter_drone", "military_ai", "dual_use_tech",
        "border_security", "physical_security",
    ],
    "b2b_software": [
        "devtools", "data_infra", "cybersecurity_saas", "hr_tech", "legal_tech",
        "supply_chain", "procurement", "marketing_tech", "sales_tech",
        "customer_success", "vertical_saas", "api_first", "no_code_low_code",
        "cloud_infra", "observability", "identity_access", "erp_ops",
    ],
    "consumer": [
        "consumer_health", "gaming", "creator_economy", "edtech", "social",
        "marketplace_consumer", "d2c", "pet_tech", "home_tech", "travel_tech",
        "food_delivery", "sports_tech", "fashion_tech", "consumer_finance",
    ],
    "other_verticals": [
        "proptech", "legaltech", "govtech", "smart_cities", "future_of_work",
        "construction_tech", "logistics", "mobility", "agtech", "precision_agriculture",
        "impact_investing", "generalist",
    ],
}

# Flat list for the prompt
ALL_TAGS = [tag for tags in TAXONOMY.values() for tag in tags]
TAXONOMY_STR = "\n".join(
    f"  {group}: {', '.join(tags)}"
    for group, tags in TAXONOMY.items()
)

# ── Prompts ────────────────────────────────────────────────────────────────

SYSTEM = """\
You are a venture capital analyst specializing in investor categorization.
Read each investor's thesis and portfolio companies, then assign precise
sub-sector tags describing what they ACTUALLY focus on.

Be specific — infer from portfolio company names when the thesis is generic.
Output ONLY valid JSON, no prose."""

BATCH_USER = """\
Assign 3–8 sub_tags to each investor from the approved taxonomy below.

APPROVED TAGS (only use these exact strings):
{taxonomy}

Rules:
- Infer from portfolio companies when thesis is vague
  e.g. portfolio has "Recursion Pharmaceuticals" → ai_drug_discovery
  e.g. portfolio has "KoBold Metals" → carbon_capture, ai_for_science
- Use "generalist" only when truly no pattern is discernible
- Never invent tags outside the approved list
- Return exactly this JSON structure:

{{
  "<investor_id>": ["tag1", "tag2", "tag3"],
  "<investor_id>": ["tag1", "tag2"],
  ...
}}

INVESTORS:
{investors_json}"""

# ── Helpers ────────────────────────────────────────────────────────────────

def _summarize(inv: dict) -> dict:
    """Compact investor summary to fit in batch prompt."""
    portfolio = inv.get("portfolio_companies") or []
    portfolio_str = ", ".join(
        f"{p['name']}({p.get('sector') or ''})"
        for p in portfolio[:15]
        if p.get("name")
    )
    return {
        "id":        inv.get("id") or inv.get("name", ""),
        "name":      inv.get("name", ""),
        "sectors":   inv.get("sectors") or [],
        "thesis":    (inv.get("thesis_text") or "")[:600],
        "portfolio": portfolio_str[:400] or None,
    }


def _call_claude(batch: list[dict]) -> dict[str, list[str]]:
    """Call Claude Haiku to generate sub_tags for one batch."""
    summaries     = [_summarize(inv) for inv in batch]
    investors_json = json.dumps(summaries, ensure_ascii=False, indent=2)
    prompt         = BATCH_USER.format(
        taxonomy=TAXONOMY_STR,
        investors_json=investors_json,
    )

    for attempt in range(MAX_RETRIES):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=1500,
                temperature=0.1,
                system=SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```\s*$",       "", raw)

            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("Expected dict")
            return result

        except json.JSONDecodeError as e:
            print(f"      JSON error attempt {attempt+1}: {e}")
            if attempt == MAX_RETRIES - 1:
                return {
                    (inv.get("id") or inv.get("name", "")): ["enrichment_failed"]
                    for inv in batch
                }
        except anthropic.APIStatusError as e:
            print(f"      API error attempt {attempt+1}: HTTP {e.status_code}")
            if e.status_code == 429:
                time.sleep(15)
            if attempt == MAX_RETRIES - 1:
                return {}
        except Exception as e:
            print(f"      Error attempt {attempt+1}: {str(e)[:100]}")
            if attempt == MAX_RETRIES - 1:
                return {}

    return {}


def _clean_tags(raw_tags: list) -> list[str]:
    """Validate and clean tags — keep only approved vocabulary."""
    valid = set(ALL_TAGS)
    cleaned = []
    for t in raw_tags:
        if not isinstance(t, str):
            continue
        t = t.lower().strip().replace(" ", "_").replace("-", "_")
        if t in valid:
            cleaned.append(t)
        # else: silently drop unknown tags
    return cleaned or ["generalist"]


def _load() -> list[dict]:
    investors = []
    with open(JSONL_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                investors.append(json.loads(line))
    return investors


def _save(investors: list[dict]) -> None:
    tmp = JSONL_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for inv in investors:
            f.write(json.dumps(inv, ensure_ascii=False) + "\n")
    tmp.replace(JSONL_PATH)

# ── Main ───────────────────────────────────────────────────────────────────

def run(force: bool, dry_run: bool, sample: int) -> None:
    all_investors = _load()   # always load full file

    if sample:
        to_enrich = all_investors[:sample]
        print(f"🔬 Sample mode: {sample} investors only (file NOT overwritten)")
    elif force:
        to_enrich = all_investors
    else:
        to_enrich = [i for i in all_investors if not i.get("sub_tags")]

    if not to_enrich:
        print("✅  All investors already have sub_tags. Use --force to re-enrich.")
        return

    n_batches = (len(to_enrich) + BATCH_SIZE - 1) // BATCH_SIZE
    est_cost  = len(to_enrich) * 0.0005

    print(f"📋  Investors to enrich : {len(to_enrich)}")
    print(f"    Batches             : {n_batches}  (size {BATCH_SIZE})")
    print(f"    Model               : {MODEL}")
    print(f"    Estimated cost      : ~${est_cost:.2f}")
    print()

    if dry_run:
        print("🔍  Dry-run — showing first 3 investor summaries:")
        print(json.dumps([_summarize(i) for i in to_enrich[:3]],
                         ensure_ascii=False, indent=2))
        return

    # Build id → investor index from FULL list (so _save writes all records)
    inv_index: dict[str, dict] = {}
    for inv in all_investors:
        key = inv.get("id") or inv.get("name", "")
        inv_index[key] = inv

    total_done = 0

    for b_start in range(0, len(to_enrich), BATCH_SIZE):
        batch    = to_enrich[b_start : b_start + BATCH_SIZE]
        b_num    = b_start // BATCH_SIZE + 1
        b_end    = min(b_start + BATCH_SIZE, len(to_enrich))

        print(f"  Batch {b_num:>3}/{n_batches}  ({b_start+1}–{b_end} of {len(to_enrich)})")

        results = _call_claude(batch)

        for inv in batch:
            inv_id   = inv.get("id") or inv.get("name", "")
            raw_tags = (results.get(inv_id)
                        or results.get(inv.get("name", ""))
                        or [])
            tags = _clean_tags(raw_tags)

            if inv_id in inv_index:
                inv_index[inv_id]["sub_tags"] = tags
                total_done += 1

            # Print a few samples per batch
            if b_start == 0 and batch.index(inv) < 3:
                print(f"    {inv['name'][:42]:<42} → {tags}")

        if b_start + BATCH_SIZE < len(to_enrich):
            time.sleep(RATE_DELAY)

    # Persist
    print()
    if sample:
        print("🔬 Sample mode: skipping file write. Results:")
        for inv in to_enrich:
            key = inv.get("id") or inv.get("name", "")
            print(f"  {inv.get('name','')[:42]:<42} sub_tags={inv_index[key].get('sub_tags')}")
        return

    print(f"💾  Writing {total_done} enriched records → {JSONL_PATH.name}")
    _save(list(inv_index.values()))

    # Stats
    all_tags: list[str] = []
    for inv in inv_index.values():
        all_tags.extend(inv.get("sub_tags") or [])

    top20 = Counter(all_tags).most_common(20)
    print(f"\n✅  Done.  {total_done} investors enriched.\n")
    print("Top 20 sub_tags assigned:")
    for tag, cnt in top20:
        bar = "█" * (cnt // 4)
        print(f"  {tag:<32}  {cnt:3d}  {bar}")


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Enrich investor DB with sub_tags via Claude Haiku"
    )
    ap.add_argument("--force",   action="store_true",
                    help="Re-enrich all, even those with existing sub_tags")
    ap.add_argument("--dry-run", action="store_true",
                    help="Preview first batch input without calling API")
    ap.add_argument("--sample",  type=int, default=0,
                    help="Process only first N investors (testing)")
    args = ap.parse_args()

    run(force=args.force, dry_run=args.dry_run, sample=args.sample)
