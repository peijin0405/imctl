#!/usr/bin/env python3
"""
Clean existing investors.json in 3 strict steps, then apply focus whitelist.

  Step 1 — Remove invalid names (too long / bad phrase / too short)
  Step 2 — Remove non-investment-institutions (no recognised firm suffix)
  Step 3 — Remove obvious portfolio companies (startup-characteristic endings)
"""
import json
import os
import re
import shutil
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
from exa_search import clean_name, deduplicate

DATA_PATH     = os.path.join(os.path.dirname(__file__), "..", "data", "investors.json")
WEB_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "web",  "investors.json")

# ── Focus whitelist per sector ────────────────────────────────────────────────
FOCUS_WHITELIST: dict[str, frozenset[str]] = {
    "healthcare": frozenset({
        "biotech", "drug discovery", "digital health", "medtech",
        "genomics", "oncology", "precision medicine", "mental health",
        "diagnostics", "medical device", "cell therapy", "gene therapy",
        "health insurance", "telehealth", "population health", "bioinformatics",
        "clinical trials", "rare disease", "neuroscience", "immunotherapy",
    }),
    "ai_apps": frozenset({
        "artificial intelligence", "machine learning", "generative ai",
        "ai agent", "nlp", "computer vision", "deep learning", "llm",
        "enterprise software", "saas", "automation", "robotics",
        "ai infrastructure", "mlops", "data analytics", "conversational ai",
    }),
    "ai_hardware": frozenset({
        "robotics", "humanoid robot", "embodied ai", "edge ai",
        "autonomous systems", "industrial automation", "computer vision",
        "hardware", "sensors", "drone", "semiconductor", "gpu", "npu",
    }),
    "semiconductor": frozenset({
        "chip design", "eda", "gpu", "npu", "hbm", "advanced packaging",
        "gan", "sic", "silicon", "photonics", "mems", "power semiconductor",
        "semiconductor equipment", "fabless", "foundry", "ic design",
    }),
    "fintech": frozenset({
        "fintech", "blockchain", "defi", "payments", "insurtech",
        "wealthtech", "regtech", "neobank", "cryptocurrency", "lending",
        "embedded finance", "digital assets", "fraud detection",
    }),
    "edtech": frozenset({
        "edtech", "e-learning", "online education", "adaptive learning",
        "upskilling", "corporate training", "language learning", "k12",
        "higher education", "vocational training", "lms",
    }),
    "greentech": frozenset({
        "clean energy", "carbon capture", "circular economy",
        "advanced materials", "bio-based materials", "sustainable chemistry",
        "water treatment", "waste management", "recycling", "esg",
        "climate tech", "nanotechnology", "composite materials",
    }),
    "energy": frozenset({
        "solar", "wind", "energy storage", "battery", "hydrogen",
        "fuel cell", "smart grid", "nuclear", "ev", "electric vehicle",
        "charging infrastructure", "renewable energy", "clean energy",
        "energy management", "smr",
    }),
}

# ── Step 1: bad-phrase / length rules ─────────────────────────────────────────
# These are sentence-fragment indicators — legitimate firm names never contain them.
STEP1_BAD_PHRASES: frozenset[str] = frozenset({
    "invests in", "investing", "therapies to", "led by",
    "from capital", "co-led by", "participation from",
    "skip navigation", "listen to", "announcing", "why we",
    "reach out", "home page", "co-incubated", "gained access",
    "expands", "increase access", "great", "approach", "page",
    "flipping", "greatness", "statement", "about us",
})

# ── Step 2: firm-suffix whitelist ─────────────────────────────────────────────
FIRM_SUFFIX_WORDS: frozenset[str] = frozenset({
    "capital", "ventures", "venture", "fund", "partners", "vc", "investments",
    "group", "asset", "equity", "holdings", "management", "advisors", "labs",
    "backed", "studio", "angels", "associates", "growth", "innovation",
    "nexus", "bridge", "gateway", "alliance", "network", "collective",
})

# Well-known institutions kept even without a suffix word
KNOWN_INSTITUTION_KEYWORDS: frozenset[str] = frozenset({
    "sequoia", "andreessen", "khosla", "softbank", "temasek", "gic",
    "blackrock", "goldman", "jpmorgan", "nvidia", "intel", "google",
    "microsoft", "samsung", "qualcomm",
})

# ── Step 3: startup-company suffix endings ────────────────────────────────────
STARTUP_SUFFIX_ENDINGS: frozenset[str] = frozenset({
    "therapeutics", "surgical", "medicines", "biosciences", "oncology",
    "robotics", "genomics",
})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _word_set(name: str) -> set[str]:
    """Lowercase alphabetic tokens from space-split words (strips punctuation)."""
    return {re.sub(r"[^a-z]", "", w.lower()) for w in name.split()} - {""}


def _last_alpha_word(name: str) -> str:
    """Last purely-alphabetic run in the name, lowercased."""
    tokens = re.findall(r"[a-zA-Z]+", name)
    return tokens[-1].lower() if tokens else ""


# ── Step predicates (return True → record is REMOVED) ────────────────────────

def is_step1_invalid(name: str) -> bool:
    if len(name) < 3:
        return True
    if len(name.split()) > 5:
        return True
    name_lower = name.lower()
    return any(phrase in name_lower for phrase in STEP1_BAD_PHRASES)


def is_step2_non_institution(name: str) -> bool:
    name_lower = name.lower()
    if any(kw in name_lower for kw in KNOWN_INSTITUTION_KEYWORDS):
        return False
    return not (_word_set(name) & FIRM_SUFFIX_WORDS)


def is_step3_portfolio_company(name: str) -> bool:
    return _last_alpha_word(name) in STARTUP_SUFFIX_ENDINGS


# ── Supporting helpers ────────────────────────────────────────────────────────

def _apply_step(
    records: list[dict],
    predicate,
) -> tuple[list[dict], list[dict]]:
    kept, removed = [], []
    for rec in records:
        (removed if predicate(rec["name"]) else kept).append(rec)
    return kept, removed


def _print_step(step: int, label: str, removed: list[dict]) -> None:
    print(f"── Step {step}: {label}")
    print(f"   Removed : {len(removed)}")
    for r in removed[:10]:
        print(f"   - {r['name']!r}")
    print()


def clean_focus(focus: list[str], sector: str) -> list[str]:
    whitelist = FOCUS_WHITELIST.get(sector)
    if whitelist is None:
        return focus
    return [f for f in focus if f.lower() in whitelist]


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    with open(DATA_PATH, encoding="utf-8") as f:
        records: list[dict] = json.load(f)

    before = len(records)
    print(f"Records loaded          : {before}")
    print()

    # Pre-transform: normalise names with the existing clean_name utility
    pre_cleaned: list[dict] = []
    for rec in records:
        raw = (rec.get("name") or "").strip()
        new_name = clean_name(raw)
        if new_name:
            rec = dict(rec)
            rec["name"] = new_name
            pre_cleaned.append(rec)
    records = pre_cleaned

    # ── Step 1 ────────────────────────────────────────────────────────────────
    records, s1_removed = _apply_step(records, is_step1_invalid)
    _print_step(1, "Invalid names (>5 words / bad phrase / <3 chars)", s1_removed)

    # ── Step 2 ────────────────────────────────────────────────────────────────
    records, s2_removed = _apply_step(records, is_step2_non_institution)
    _print_step(2, "Non-investment-institutions (no firm suffix word)", s2_removed)

    # ── Step 3 ────────────────────────────────────────────────────────────────
    records, s3_removed = _apply_step(records, is_step3_portfolio_company)
    _print_step(3, "Obvious portfolio companies (startup-characteristic ending)", s3_removed)

    # ── Focus cleaning ────────────────────────────────────────────────────────
    for rec in records:
        rec["focus"] = clean_focus(rec.get("focus") or [], rec.get("sector", ""))

    # ── Deduplication ─────────────────────────────────────────────────────────
    records = deduplicate(records)
    after = len(records)

    # ── Summary report ────────────────────────────────────────────────────────
    print("── Summary")
    print(f"   Step 1 removed       : {len(s1_removed)}")
    print(f"   Step 2 removed       : {len(s2_removed)}")
    print(f"   Step 3 removed       : {len(s3_removed)}")
    print(f"   Final records kept   : {after}  (removed {before - after} total)")
    print()

    sector_counts = Counter(r.get("sector") or "unknown" for r in records)
    print("── Distribution by sector")
    for sector, count in sorted(sector_counts.items(), key=lambda x: -x[1]):
        print(f"   {sector:<22}: {count}")

    # ── Save ──────────────────────────────────────────────────────────────────
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    shutil.copy2(DATA_PATH, WEB_DATA_PATH)
    print(f"\nSaved {after} records → data/ and web/")


if __name__ == "__main__":
    main()
