"""
Investor Matching Engine — scraper/m_matcher.py

Three-tier matching against investor DB:

  Tier 1  Hard gate     — stage / amount / sector / geo hard filters
  Tier 2  Soft score    — business model, traction, team, lead, geo (weighted)
  Tier 3  Semantic      — cosine similarity between BP narrative and investor thesis

Public API:
    match(bp_profile: dict, top_n: int = 15) -> list[dict]

CLI:
    python scraper/m_matcher.py --profile output/business_profile.json
    python scraper/m_matcher.py --profile output/business_profile.json --top 20 --no-semantic
"""

import json
import math
import os
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────────

ROOT       = Path(__file__).parent.parent
DATA_DIR   = Path(os.getenv("DATA_DIR", str(ROOT)))
DB_PATH    = DATA_DIR / "enrichment_target" / "active.jsonl"
JSONL_PATH = DATA_DIR / "enrichment_target" / "active.jsonl"

# ── Claude client (for embeddings) ────────────────────────────────────────

_api_key = os.getenv("ANTHROPIC_API_KEY")
_client  = anthropic.Anthropic(api_key=_api_key) if _api_key else None

EMBED_MODEL = "voyage-3"   # via Anthropic's Voyage API

# ── Tier 2 component weights (must sum to 1.0) ─────────────────────────────
WEIGHTS = {
    "business_model": 0.25,
    "traction":       0.30,
    "team":           0.20,
    "lead":           0.10,
    "geo_soft":       0.15,
}

# ── Sector synonym map ─────────────────────────────────────────────────────

TAG_SYNONYMS: dict[str, str] = {
    "bio":                "biotech",
    "biopharma":          "biotech",
    "biopharmaceuticals": "biotech",
    "life_sciences":      "biotech",
    "pharmaceutical":     "biotech",
    "hardware":           "deeptech",
    "semiconductor":      "deeptech",
    "robotics":           "deeptech",
    "healthtech":         "healthcare",
    "medtech":            "healthcare",
    "digital_health":     "healthcare",
    "health_it":          "healthcare",
    "gene_editing":        "gene_therapy",
    "crispr":              "gene_therapy",
    "genomics":            "gene_therapy",
    "genome_engineering":  "gene_therapy",
    "cell_and_gene":       "cell_therapy",
    "allogeneic":          "cell_therapy",
    "car_t":               "cell_therapy",
    "cell_biology":        "cell_therapy",
    "immuno_oncology":     "immunotherapy",
    "cancer_immunology":   "immunotherapy",
    "oncology_biotech":    "oncology",
    "industrial_bio":      "synthetic_biology",
    "synthetic_bio":       "synthetic_biology",
    "synbio":              "synthetic_biology",
    "bioplatform":         "platform_biotech",
}

def _normalize_tags(tags: list[str]) -> set[str]:
    return {TAG_SYNONYMS.get(t, t) for t in tags}


# ── Intent compatibility taxonomy ─────────────────────────────────────────

DOMAIN_TAXONOMY: dict[str, dict] = {
    "biomedical": {
        "bp_tags": {"biotech", "healthcare"},
        "thesis_signals": {
            "biotech", "biopharma", "therapeutic", "cell therapy", "gene therapy",
            "crispr", "oncology", "immunotherapy", "drug discovery", "clinical",
            "life science", "genomics", "diagnostics", "medtech", "biologic",
            "pharma", "health", "medical",
        },
        "strong_excluders": {
            "bioindustrial", "bioremediation", "de-extinction",
            "conservation", "carbon capture", "carbon sequestration",
            "biomanufacturing for", "metal processing",
            "agrifood", "food and ag", "food and agriculture",
            "food system", "food economy", "food supply chain",
            "crop science", "animal health", "food",
        },
        "weak_excluders": {
            "climate", "industrial", "manufacturing",
            "agriculture", "biofuel", "mining",
        },
    },
    "climate_cleantech": {
        "bp_tags": {"climate", "cleantech"},
        "thesis_signals": {
            "climate", "clean energy", "solar", "wind", "battery", "electric vehicle",
            "carbon", "renewable", "sustainability", "energy storage", "grid",
            "electrification", "decarbonization", "cleantech",
        },
        "strong_excluders": {
            "therapeutics", "clinical trial", "drug discovery",
            "oncology", "patient outcomes",
        },
        "weak_excluders": {
            "pharma", "hospital", "medical device",
        },
    },
    "saas_software": {
        "bp_tags": {"saas", "ai_ml", "ai_infra", "devtools"},
        "thesis_signals": {
            "saas", "software", "enterprise", "devtools", "cloud", "api",
            "platform", "b2b", "productivity", "infrastructure",
        },
        "strong_excluders": {
            "therapeutics", "clinical", "bioindustrial",
            "mine", "carbon capture",
        },
        "weak_excluders": {
            "hardware", "manufacturing", "agriculture",
        },
    },
    "fintech": {
        "bp_tags": {"fintech"},
        "thesis_signals": {
            "fintech", "payments", "banking", "lending", "insurance", "financial",
            "crypto", "defi", "wealth management", "capital markets",
        },
        "strong_excluders": {
            "therapeutics", "clinical trial", "bioindustrial",
            "carbon capture", "bioremediation",
        },
        "weak_excluders": {
            "biotech", "climate hardware", "physical sciences",
            "consumer packaged goods",
        },
    },
    "deeptech_hardware": {
        "bp_tags": {"deeptech", "hardware"},
        "thesis_signals": {
            "deeptech", "hardware", "semiconductor", "quantum", "robotics",
            "defense", "space", "advanced manufacturing", "photonics", "sensors",
        },
        "strong_excluders": {
            "consumer apps only", "saas only", "fintech only",
            "marketplace only", "therapeutics only",
        },
        "weak_excluders": {
            "consumer apps", "saas", "fintech", "marketplace",
        },
    },
}


def _detect_bp_domain(bp: dict) -> str | None:
    """Return the DOMAIN_TAXONOMY key that best fits the BP's sector tags, or None."""
    bp_sectors_raw = _get_bp_sector_list(bp)
    if not bp_sectors_raw:
        return None
    bp_set = _normalize_tags([s.lower() for s in bp_sectors_raw])
    best_domain, best_count = None, 0
    for domain, spec in DOMAIN_TAXONOMY.items():
        count = len(bp_set & spec["bp_tags"])
        if count > best_count:
            best_count, best_domain = count, domain
    return best_domain if best_count >= 1 else None


def _intent_compatible(bp: dict, investor: dict) -> tuple[bool, float]:
    """
    Check whether an investor's thesis is directionally compatible with the BP's domain.
    Returns (compatible: bool, intent_mult: float).
    intent_mult is applied on top of sector_mult.
    """
    bp_domain = _detect_bp_domain(bp)
    if bp_domain is None:
        return True, 1.0

    spec   = DOMAIN_TAXONOMY[bp_domain]
    thesis = (investor.get("thesis_text") or "").lower()

    strong_hits = sum(1 for t in spec["strong_excluders"] if t in thesis)
    weak_hits   = sum(1 for t in spec["weak_excluders"]   if t in thesis)
    thesis_hits = sum(1 for t in spec["thesis_signals"]   if t in thesis)

    if strong_hits >= 2:
        return False, 0.0   # hard drop — two confirmed off-domain signals
    if strong_hits == 1:
        return True, 0.4    # strong soft penalty
    if weak_hits >= 2 and thesis_hits == 0:
        return False, 0.0   # original logic preserved
    if weak_hits >= 1 and thesis_hits == 0:
        return True, 0.6    # weak soft penalty
    return True, 1.0


# ── Geo helpers ────────────────────────────────────────────────────────────

CITY_TO_GEO: dict[str, str] = {
    "san francisco": "sf_bay_area", "sf": "sf_bay_area",
    "palo alto": "sf_bay_area", "menlo park": "sf_bay_area",
    "mountain view": "sf_bay_area", "san jose": "sf_bay_area",
    "oakland": "sf_bay_area", "berkeley": "sf_bay_area",
    "silicon valley": "sf_bay_area",
    "new york": "nyc", "new york city": "nyc", "ny": "nyc", "nyc": "nyc",
    "boston": "boston", "cambridge": "boston",
    "austin": "austin",
    "los angeles": "la", "la": "la",
    "seattle": "seattle",
    "chicago": "chicago",
}

def _bp_geo_tag(bp: dict) -> str | None:
    city = (bp.get("geography") or "").lower().split(",")[0].strip()
    return CITY_TO_GEO.get(city)

def _hq_country(bp: dict) -> str:
    try:
        return (bp["_profile"]["tier1"]["geo"]["hq_country"] or "US")
    except (KeyError, TypeError):
        return "US"

def _geo_compatible(bp: dict, investor: dict) -> bool:
    geo_focus: list = investor.get("geo_focus") or []
    if not geo_focus:
        return True
    if "global" in geo_focus or "us_agnostic" in geo_focus:
        return True
    bp_geo = _bp_geo_tag(bp)
    if bp_geo and bp_geo in geo_focus:
        return True
    country = _hq_country(bp).upper()
    if country not in ("US", "USA", "UNITED STATES"):
        return "global" in geo_focus
    return False


# ── Tier 1: hard gates ─────────────────────────────────────────────────────

def _bp_stage(bp: dict) -> str | None:
    try:
        return bp["_profile"]["tier1"]["funding_stage"]["value"]
    except (KeyError, TypeError):
        return bp.get("funding_stage")

def _stage_compatible(bp_stage: str | None, investor_stages: list) -> bool:
    if not bp_stage or not investor_stages:
        return True
    return bp_stage in investor_stages

def _amount_penalty(bp: dict, investor: dict) -> float:
    inv_min = investor.get("check_size_min_usd")
    inv_max = investor.get("check_size_max_usd")
    if inv_min is None and inv_max is None:
        return 1.0
    try:
        amt    = bp["_profile"]["tier1"]["raise_amount_usd"]
        bp_min = amt.get("value_min") or amt.get("value")
        bp_max = amt.get("value_max") or amt.get("value")
    except (KeyError, TypeError):
        return 1.0   # BP missing amount → no penalty
    if bp_min is None and bp_max is None:
        return 1.0
    bp_min  = bp_min  or 0
    bp_max  = bp_max  or bp_min
    inv_min = inv_min or 0
    inv_max = inv_max or float("inf")
    if bp_max >= inv_min and bp_min <= inv_max:
        return 1.0
    ratio = max(
        inv_min / bp_max if bp_max else float("inf"),
        bp_min / inv_max if inv_max else float("inf"),
    )
    if ratio <= 3:
        return 0.7
    if ratio <= 8:
        return 0.4
    return 0.0

def _get_bp_sector_list(bp: dict) -> list[str]:
    """Extract BP sector tags across all profile formats (wrapped, raw JSON, flat)."""
    # Format 1: legacy _profile wrapper
    try:
        v = bp["_profile"]["tier1"]["sector_tags"]["value"]
        if v:
            return v
    except (KeyError, TypeError):
        pass
    # Format 2: raw JSON from parser (bp["tier1"]["sector_tags"]["value"])
    try:
        v = bp["tier1"]["sector_tags"]["value"]
        if v:
            return v
    except (KeyError, TypeError):
        pass
    # Format 3: flat profile (sector_tags is a comma-sep string, key = "sector_tags")
    raw = bp.get("sector_tags") or bp.get("sector") or ""
    if isinstance(raw, str) and raw.strip():
        return [s.strip() for s in raw.split(",") if s.strip()]
    return []


def _sector_gate_and_jaccard(bp: dict, investor: dict) -> float:
    """
    Coarse-grained sector matching. Returns Jaccard score 0.0–1.0.
    0.0 means hard exclusion (no overlap at all).
    Both sides are normalized to canonical tags before comparison.
    """
    inv_sectors: list = investor.get("sectors") or []
    if not inv_sectors:
        return 1.0
    bp_sectors_raw = _get_bp_sector_list(bp)
    if not bp_sectors_raw:
        return 1.0
    bp_set       = _normalize_tags(bp_sectors_raw)
    inv_set      = _normalize_tags(inv_sectors)
    intersection = bp_set & inv_set
    if not intersection:
        return 0.0
    union    = bp_set | inv_set
    jaccard  = len(intersection) / len(union)
    recall   = len(intersection) / len(bp_set)
    return 0.75 * recall + 0.25 * jaccard


# ── Portfolio-based sub-sector inference ──────────────────────────────────

# Keyword → canonical sub-sector tag. Keys are substrings to match in
# portfolio company names and founder_background descriptions.
PORTFOLIO_KEYWORD_MAP: dict[str, str] = {
    "xcell":          "cell_therapy",
    "cell line":      "cell_therapy",
    "cell bio":       "cell_therapy",
    "reprogramm":     "cell_therapy",   # cellular reprogramming
    "car-t":          "cell_therapy",
    "car t":          "cell_therapy",
    "genomics":       "gene_therapy",
    "sequencing":     "gene_therapy",
    "genome":         "gene_therapy",
    "crispr":         "gene_therapy",
    "base edit":      "gene_therapy",
    "gene edit":      "gene_therapy",
    "gene therap":    "gene_therapy",
    "longevity":      "longevity",
    "aging":          "longevity",
    "lifespan":       "longevity",
    "oncology":       "oncology",
    "tumor":          "oncology",
    "cancer":         "oncology",
    "immunotherapy":  "immunotherapy",
    "immuno-oncology":"immunotherapy",
    "synthetic bio":  "synthetic_biology",
}


def _infer_sub_sectors_from_portfolio(investor: dict) -> tuple[list[str], str]:
    """
    Scan portfolio company names and descriptions for sub-sector signals.
    Returns (inferred_tags, source) where source is "inferred" or "none".
    Used as a rescue when sub_tags are absent or produce a zero-overlap mismatch.
    """
    portfolio = investor.get("portfolio_companies") or []
    if not portfolio:
        return [], "none"

    # Build a single searchable text from all portfolio entries
    parts: list[str] = []
    for co in portfolio:
        parts.append((co.get("name") or "").lower())
        parts.append((co.get("founder_background") or "").lower())
        parts.append((co.get("sector") or "").lower())
    combined = " ".join(parts)

    inferred: set[str] = set()
    for keyword, tag in PORTFOLIO_KEYWORD_MAP.items():
        if keyword in combined:
            inferred.add(tag)

    if inferred:
        return sorted(inferred), "inferred"
    return [], "none"


def _sub_sector_bonus(bp: dict, investor: dict) -> dict:
    """
    Fine-grained sub_sector signal. Returns a dict used as the scoring anchor.

    Returns:
      {
        "base":         float,   # primary anchor score (0.0–1.0)
        "f1":           float,   # raw F1 (0.0 when no overlap)
        "status":       str,     # matched | inferred | no_data | confirmed_mismatch
        "tags_matched": list[str]
      }

    Base values by status:
      matched / inferred:         base = f1  (0.0–1.0)
      no_data:                    base = 0.35  (neutral prior)
      confirmed_mismatch:         base = 0.03  (heavy penalisation)

    Inference is triggered when:
      - investor sub_tags is empty (or < 2 tags), OR
      - investor sub_tags produce zero overlap with BP (rescue attempt)

    Special case: investor tagged 'generalist' → treated as no_data.
    """
    # ── Extract BP sub_tags (try all three profile formats) ──────────────────
    bp_sub = []
    for path in (
        lambda: bp["_profile"]["tier2"]["sub_sector_tags"]["value"],
        lambda: bp["tier2"]["sub_sector_tags"]["value"],
    ):
        try:
            v = path()
            if v:
                bp_sub = [t.strip().lower() for t in v if t]
                break
        except (KeyError, TypeError):
            pass
    if not bp_sub:
        raw = bp.get("sub_sector_tags") or ""
        if isinstance(raw, str) and raw.strip():
            bp_sub = [t.strip().lower() for t in raw.split(",") if t.strip()]

    inv_sub = list(_normalize_tags([t.lower() for t in (investor.get("sub_tags") or [])]))

    def _no_data():
        return {"base": 0.20, "f1": 0.0, "status": "no_data", "tags_matched": []}

    def _mismatch():
        return {"base": 0.03, "f1": 0.0, "status": "confirmed_mismatch", "tags_matched": []}

    def _matched(overlap, effective_sub, status):
        precision  = len(overlap) / len(bp_sub)          # BP视角：覆盖了我多少需求
        hit_bonus  = min(len(overlap) / 3.0, 1.0)        # 命中数奖励，3个封顶为1.0
        base_score = round(precision * 0.7 + hit_bonus * 0.3, 4)
        # 保留 f1 字段供调试用，但不再作为 base
        recall = len(overlap) / len(effective_sub) if effective_sub else 0
        f1     = round(2 * precision * recall / (precision + recall), 4) if (precision + recall) else 0.0
        return {"base": base_score, "f1": f1,
                "status": status, "tags_matched": sorted(overlap)}

    # ── no_data: generalist-only investor (strip generalist when other tags exist) ─
    if "generalist" in inv_sub:
        non_generalist = [t for t in inv_sub if t != "generalist"]
        if non_generalist:
            inv_sub = non_generalist
        else:
            return _no_data()

    # ── no_data: BP has no sub-sector tags (parser didn't extract them) ───────
    if not bp_sub:
        return _no_data()

    # ── Trigger inference when tags are absent or sparse (< 2) ───────────────
    inferred_source = None
    if len(inv_sub) < 2:
        extra, src = _infer_sub_sectors_from_portfolio(investor)
        if extra:
            inv_sub = list(set(inv_sub) | set(extra))
            inferred_source = src

    # ── Compute overlap on current inv_sub ───────────────────────────────────
    overlap = set(bp_sub) & set(inv_sub)

    # ── On mismatch, try portfolio inference as a rescue ─────────────────────
    if not overlap and not inferred_source:
        extra, src = _infer_sub_sectors_from_portfolio(investor)
        if extra:
            inv_sub = list(set(inv_sub) | set(extra))
            inferred_source = src
            overlap = set(bp_sub) & set(inv_sub)

    # ── Resolve final status ─────────────────────────────────────────────────
    if not inv_sub:
        return _no_data()

    if not overlap:
        return _mismatch()

    status = "inferred" if inferred_source else "matched"
    return _matched(overlap, inv_sub, status)


# ── Tier 2: soft scorers ───────────────────────────────────────────────────

def _score_business_model(bp: dict, investor: dict) -> float:
    inv_prefs: list = investor.get("business_model_preference") or []
    if not inv_prefs:
        return 0.5   # investor has no stated preference → neutral
    try:
        bp_models = bp["_profile"]["tier2"]["business_model"]["value"] or []
    except (KeyError, TypeError):
        raw = bp.get("business_model", "")
        bp_models = [s.strip() for s in raw.split(",")] if raw else []
    if not bp_models:
        return 0.2
    overlap = set(bp_models) & set(inv_prefs)
    union   = set(bp_models) | set(inv_prefs)
    return len(overlap) / len(union)   # Jaccard: 0 → 1.0


def _score_traction(bp: dict, investor: dict) -> float:
    req            = investor.get("traction_requirement") or {}
    pre_revenue_ok = req.get("pre_revenue_ok")   # True / False / None
    min_arr        = req.get("min_arr_usd")

    try:
        traction = bp["_profile"]["tier2"]["traction"]
    except (KeyError, TypeError):
        traction = {}

    bp_pre = traction.get("pre_revenue", False) if isinstance(traction, dict) else False
    bp_arr = traction.get("arr_usd")             if isinstance(traction, dict) else None

    # Hard incompatibility: investor requires revenue, BP is pre-revenue
    if pre_revenue_ok is False and bp_pre:
        return 0.0

    # Investor has ARR floor
    if min_arr:
        if bp_arr is None:
            return 0.2           # floor exists but BP didn't disclose
        if bp_arr >= min_arr:
            return 1.0
        return max(0.1, bp_arr / min_arr)   # linear decay

    # No ARR floor — score by BP's actual traction level
    if bp_arr and bp_arr >= 1_000_000:
        return 1.0    # $1M+ ARR
    if bp_arr and bp_arr >= 100_000:
        return 0.85   # early revenue
    if bp_arr and bp_arr > 0:
        return 0.7    # some revenue
    if not bp_pre:
        return 0.6    # has revenue, no specific number
    if pre_revenue_ok is True:
        return 0.5    # pre-revenue, investor explicitly ok
    if pre_revenue_ok is None:
        return 0.3    # pre-revenue, investor preference unknown
    return 0.2


def _score_team(bp: dict, investor: dict) -> float:
    inv_prefs: list = investor.get("team_background_preference") or []
    if not inv_prefs:
        return 0.5
    try:
        bp_bg = bp["_profile"]["tier2"]["team_background"]["value"] or []
    except (KeyError, TypeError):
        raw = bp.get("team_background", "")
        bp_bg = [s.strip() for s in raw.split(",")] if raw else []
    if not bp_bg:
        return 0.2

    overlap = set(bp_bg) & set(inv_prefs)
    if not overlap:
        return 0.0

    # Jaccard base
    jaccard = len(overlap) / len(set(bp_bg) | set(inv_prefs))

    # Bonus for high-signal tags
    HIGH_VALUE = {"repeat_founder", "ex_faang", "phd_technical"}
    bonus = min(0.2, sum(0.1 for t in overlap if t in HIGH_VALUE))
    return min(1.0, jaccard + bonus)


def _score_lead(bp: dict, investor: dict) -> float:
    inv_lead = investor.get("lead_investor")  # lead_only | both | follow_ok | None
    try:
        bp_seeking = bp["_profile"]["tier1"]["seeking_lead"]["value"]
    except (KeyError, TypeError):
        bp_seeking = None

    if inv_lead is None or bp_seeking is None:
        return 0.5

    if bp_seeking is True:
        if inv_lead == "lead_only": return 1.0
        if inv_lead == "both":      return 0.85
        if inv_lead == "follow_ok": return 0.1
    if bp_seeking is False:
        if inv_lead == "follow_ok": return 1.0
        if inv_lead == "both":      return 0.7
        if inv_lead == "lead_only": return 0.2
    return 0.5


def _score_geo_soft(bp: dict, investor: dict) -> float:
    geo_focus: list = investor.get("geo_focus") or []
    if not geo_focus:
        return 0.5
    bp_geo = _bp_geo_tag(bp)
    if bp_geo and bp_geo in geo_focus:
        return 1.0
    if "us_agnostic" in geo_focus:
        return 0.7
    if "global" in geo_focus:
        return 0.6
    return 0.0


# ── Confidence multiplier ──────────────────────────────────────────────────

def _confidence_multiplier(bp: dict) -> float:
    try:
        t1 = bp["_profile"]["tier1"]
    except (KeyError, TypeError):
        return 0.8
    conf_map = {"high": 1.0, "medium": 0.85, "low": 0.65, "missing": 0.4}
    scores = []
    for field in ("funding_stage", "raise_amount_usd", "sector_tags", "geo"):
        f = t1.get(field, {})
        c = f.get("confidence", "missing") if isinstance(f, dict) else "missing"
        scores.append(conf_map.get(c, 0.4))
    return sum(scores) / len(scores) if scores else 0.8


# ── Tier 3: semantic similarity ────────────────────────────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    dot  = sum(x * y for x, y in zip(a, b))
    na   = math.sqrt(sum(x * x for x in a))
    nb   = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _embed(text: str) -> list[float] | None:
    if not text or len(text.strip()) < 20:
        return None
    voyage_key = os.getenv("VOYAGE_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    if not voyage_key:
        return None
    try:
        import voyageai
        vo  = voyageai.Client(api_key=voyage_key)
        res = vo.embed([text[:8000]], model="voyage-3", input_type="document")
        return res.embeddings[0]
    except ImportError:
        return None
    except Exception as e:
        print(f"  ⚠  Embed error: {str(e)[:80]}")
        return None


CACHE_PATH = ROOT / "scraper" / "investor_embeddings.json"

def build_investor_embedding_cache(force: bool = False) -> dict:
    """
    预计算所有 investor 的 thesis embedding，存到 investor_embeddings.json。
    下次直接读缓存，不重复调用 API。
    force=True 强制重新计算。
    """
    if CACHE_PATH.exists() and not force:
        with open(CACHE_PATH, encoding="utf-8") as f:
            cache = json.load(f)
        print(f"  ✓ Loaded {len(cache)} investor embeddings from cache")
        return cache

    investors = _load_investors()
    investors = [i for i in investors if i.get("thesis_text")]

    voyage_key = os.getenv("VOYAGE_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    if not voyage_key:
        print("  ⚠  No VOYAGE_API_KEY — skipping embedding cache build")
        return {}

    try:
        import voyageai
        vo = voyageai.Client(api_key=voyage_key)
    except ImportError:
        print("  ⚠  voyageai not installed")
        return {}

    # Load existing cache to support incremental top-up
    cache = {}
    if CACHE_PATH.exists() and not force:
        with open(CACHE_PATH, encoding="utf-8") as f:
            cache = json.load(f)

    missing = [inv for inv in investors if inv["id"] not in cache]
    if not missing:
        print(f"  ✓ All {len(cache)} investor embeddings already cached")
        return cache

    print(f"  Building embeddings for {len(missing)} investors ({len(cache)} already cached)...")
    BATCH = 64
    for i in range(0, len(missing), BATCH):
        batch = missing[i:i+BATCH]
        texts = [inv["thesis_text"][:8000] for inv in batch]
        try:
            res = vo.embed(texts, model="voyage-3", input_type="document")
            for inv, vec in zip(batch, res.embeddings):
                cache[inv["id"]] = vec
            print(f"    {min(i+BATCH, len(missing))}/{len(missing)}")
        except Exception as e:
            print(f"  ⚠  Batch {i//BATCH} failed: {e}")

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    print(f"  ✓ Saved {len(cache)} embeddings to {CACHE_PATH}")
    return cache


def _load_embedding_cache() -> dict:
    """Load cached investor embeddings, or return empty dict."""
    if CACHE_PATH.exists():
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _build_bp_embedding(bp: dict) -> list[float] | None:
    """Get or compute embedding for BP narrative text."""
    narrative = ""
    try:
        narrative = bp["_profile"]["tier3"]["narrative_text"] or ""
    except (KeyError, TypeError):
        pass
    if not narrative:
        # flat dict stores narrative_text at top level (not under tier3)
        narrative = bp.get("narrative_text") or ""
    if not narrative:
        return None
    return _embed(narrative)


def _semantic_score(bp_vec: list[float] | None,
                    investor_thesis: str,
                    investor_vec_cache: dict,
                    investor_id: str,
                    bp: dict | None = None,
                    investor: dict | None = None) -> float:
    """
    Cosine similarity between BP narrative and investor thesis.
    Falls back to keyword overlap when embedding unavailable.
    """
    if bp_vec is None:
        if bp is not None and investor is not None:
            return _keyword_overlap_score(bp, investor)
        return 0.5

    inv_vec = investor_vec_cache.get(investor_id)
    if inv_vec is None:
        if bp is not None and investor is not None:
            return _keyword_overlap_score(bp, investor)
        return 0.5

    raw = _cosine(bp_vec, inv_vec)
    # Voyage-3 cosine for VC/startup documents: unrelated ~0.15, related ~0.40-0.45
    # Map [0.20, 0.45] → [0.0, 1.0]
    normalized = (raw - 0.20) / (0.45 - 0.20)
    return max(0.0, min(1.0, normalized))


# ── Keyword-based semantic fallback ───────────────────────────────────────
# When Voyage embeddings aren't available, use keyword overlap between
# BP narrative and investor thesis as an approximate semantic signal.

def _keyword_overlap_score(bp: dict, investor: dict) -> float:
    """
    Approximate semantic match via TF-IDF-style keyword overlap.
    Returns 0.0–1.0. Used as Tier 3 fallback when embeddings fail.
    """
    try:
        narrative = bp["_profile"]["tier3"]["narrative_text"] or ""
    except (KeyError, TypeError):
        narrative = bp.get("narrative_text", "")

    thesis = investor.get("thesis_text", "") or ""

    if not narrative or not thesis:
        return 0.5

    # Stopwords to ignore
    STOP = {
        "the","a","an","and","or","of","in","to","for","with","that","this",
        "is","are","we","our","by","at","as","it","be","on","from","have",
        "has","will","can","which","their","they","who","not","but","also",
        "its","was","were","been","into","more","than","about","other","these",
        "those","when","all","one","your","company","companies","fund","capital",
        "investment","investors","portfolio","startup","startups","team","focus",
        "including","across","through","within","between","provide","provides",
    }

    def tokenize(text: str) -> set[str]:
        words = text.lower().replace(",","").replace(".","").replace(";","").split()
        return {w for w in words if len(w) > 3 and w not in STOP}

    bp_words  = tokenize(narrative)
    inv_words = tokenize(thesis)

    if not bp_words or not inv_words:
        return 0.5

    overlap = bp_words & inv_words
    # Jaccard over the two keyword sets
    jaccard = len(overlap) / len(bp_words | inv_words)

    # Normalise: jaccard for unrelated text ~0.02–0.05, related ~0.10–0.20
    # Map 0.0–0.12 → 0.0–1.0  (tighter divisor improves differentiation)
    return min(1.0, jaccard / 0.12)


# ── Disease-indication soft penalty ───────────────────────────────────────

INDICATION_LIMITERS: dict[str, list[str]] = {
    "t1d":          ["type 1 diabetes", "t1d", "diabetes"],
    "rare_disease": ["rare disease", "orphan drug", "ultra-rare"],
    "alzheimer":    ["alzheimer", "neurodegeneration", "parkinson"],
}

def _disease_indication_penalty(bp: dict, investor: dict) -> float:
    """
    If an investor's thesis is tightly focused on a specific disease indication
    that the BP does NOT address, apply a soft 40% penalty (return 0.6).
    Requires >= 2 thesis hits for the indication and 0 BP narrative hits.
    Returns 1.0 (no penalty) otherwise.
    """
    try:
        narrative = (bp["_profile"]["tier3"]["narrative_text"] or "").lower()
    except (KeyError, TypeError):
        try:
            narrative = (bp["tier3"]["narrative_text"] or "").lower()
        except (KeyError, TypeError):
            narrative = (bp.get("narrative_text") or "").lower()

    thesis = (investor.get("thesis_text") or "").lower()

    for _, signals in INDICATION_LIMITERS.items():
        thesis_hits = sum(1 for s in signals if s in thesis)
        if thesis_hits >= 2:
            bp_hits = sum(1 for s in signals if s in narrative)
            if bp_hits == 0:
                return 0.6
    return 1.0


# ── Main scorer ────────────────────────────────────────────────────────────

def _score_investor(
    bp: dict,
    investor: dict,
    bp_vec: list[float] | None,
    inv_vec_cache: dict,
    use_semantic: bool,
) -> dict | None:

    # ── Tier 1: hard gates ──────────────────────────────────────────────
    bp_stage       = _bp_stage(bp)
    sector_jaccard = _sector_gate_and_jaccard(bp, investor)

    amount_mult = _amount_penalty(bp, investor)

    t1_fails = []
    if not _stage_compatible(bp_stage, investor.get("stages") or []):
        t1_fails.append(f"stage: BP={bp_stage} not in {investor.get('stages')}")
    if amount_mult == 0.0:
        t1_fails.append("check_size out of range")
    if sector_jaccard == 0.0:
        t1_fails.append("no sector overlap")
    if not _geo_compatible(bp, investor):
        t1_fails.append("geo incompatible")

    if t1_fails:
        return None

    # ── Change 4: filter grant-givers from equity results ───────────────
    if investor.get("investor_type") == "other":
        thesis_lower = (investor.get("thesis_text") or "").lower()
        sub_url      = (investor.get("submission_url") or "")
        if "grant" in thesis_lower or sub_url.endswith("/grant-submission"):
            return None

    # ── Intent filter (domain-level compatibility) ──────────────────────
    intent_ok, intent_mult = _intent_compatible(bp, investor)
    if not intent_ok:
        return None

    # ── Tier 2: soft scores ─────────────────────────────────────────────
    dim_scores = {
        "business_model": _score_business_model(bp, investor),
        "traction":       _score_traction(bp, investor),
        "team":           _score_team(bp, investor),
        "lead":           _score_lead(bp, investor),
        "geo_soft":       _score_geo_soft(bp, investor),
    }

    # Traction hard miss → exclude
    if dim_scores["traction"] == 0.0:
        return None

    tier2_raw = sum(dim_scores[k] * WEIGHTS[k] for k in WEIGHTS)

    # Coarse sector multiplier with intent penalty on top
    sector_mult  = (0.30 + sector_jaccard * 0.70) * intent_mult  # 0.30 – 1.00
    # Investor data quality weight
    data_quality = (investor.get("tier1_completeness") or 70) / 100
    data_mult    = 0.75 + data_quality * 0.25      # 0.75 – 1.00

    tier2_score = tier2_raw * sector_mult * data_mult * amount_mult

    # ── Sub-sector: primary anchor ──────────────────────────────────────
    sub_result      = _sub_sector_bonus(bp, investor)
    base            = sub_result["base"] * _disease_indication_penalty(bp, investor)
    sub_f1          = sub_result["f1"]
    sub_sector_status = sub_result["status"]
    sub_tags_matched  = sub_result["tags_matched"]

    # ── Tier 3: semantic ────────────────────────────────────────────────
    if use_semantic and bp_vec is not None:
        inv_id = str(investor.get("id") or investor.get("name") or "")
        sem    = _semantic_score(bp_vec, investor.get("thesis_text", ""),
                                 inv_vec_cache, inv_id,
                                 bp=bp, investor=investor)
    else:
        sem = _keyword_overlap_score(bp, investor)

    # ── Final formula: base × tier2_mod × sem_mod × conf_mult ──────────
    conf_mult  = _confidence_multiplier(bp)
    tier2_mod  = 0.60 + tier2_score * 0.80   # 0.60 (t2=0) → 1.40 (t2=1)
    sem_mod    = 1.0   # semantic disabled as primary signal
    final      = round(base * tier2_mod * conf_mult, 4)

    # ── Match reasons ────────────────────────────────────────────────────
    reasons = []
    if sub_tags_matched:
        reasons.append(f"sub-sector match: {', '.join(sub_tags_matched[:3])}")
    elif sector_jaccard >= 0.4:
        reasons.append(f"sector match ({sector_jaccard:.0%})")
    if dim_scores["business_model"] >= 0.4:
        reasons.append("business model aligned")
    if dim_scores["traction"] >= 0.6:
        reasons.append("traction requirements met")
    if dim_scores["team"] >= 0.4:
        reasons.append("team background match")
    if dim_scores["lead"] >= 0.7:
        reasons.append("lead preference aligned")
    if dim_scores["geo_soft"] >= 0.9:
        reasons.append("city-level geo match")
    if sem >= 0.5:
        reasons.append(f"thesis alignment ({sem:.0%})")

    return {
        "investor_id":        investor.get("id"),
        "name":               investor.get("display_name") or investor.get("name"),
        "investor_type":      investor.get("investor_type"),
        "stages":             investor.get("stages"),
        "sectors":            investor.get("sectors"),
        "check_size":         investor.get("check_size_display"),
        "geo_focus":          investor.get("geo_focus"),
        "lead_investor":      investor.get("lead_investor"),
        "website":            investor.get("official_website"),
        "submission_url":     investor.get("submission_url"),
        "general_email":      investor.get("general_email"),
        # Scores — all preserved for UI
        "score":              final,
        "tier2_score":        round(tier2_score, 4),
        "semantic_score":     round(sem, 4),
        "sector_score":       round(sector_jaccard, 4),
        "sub_bonus":          round(sub_f1, 4),        # f1 score (replaces old additive bonus)
        "sub_f1_base":        round(base, 4),          # anchor used in final formula
        "sub_tags_matched":   sub_tags_matched,
        "sub_sector_status":  sub_sector_status,
        "tier2_mod":          round(tier2_mod, 4),
        "intent_domain":      _detect_bp_domain(bp),
        "intent_mult":        round(intent_mult, 4),
        "dim_scores":         {k: round(v, 3) for k, v in dim_scores.items()},
        "match_reasons":      reasons,
        "data_confidence":    investor.get("overall_confidence"),
    }


# ── Load DB ────────────────────────────────────────────────────────────────

def _load_investors() -> list[dict]:
    print(f"[DB] JSONL_PATH = {JSONL_PATH}", flush=True)
    print(f"[DB] JSONL exists = {JSONL_PATH.exists()}", flush=True)
    print(f"[DB] DATA_DIR env = {os.getenv('DATA_DIR', 'NOT SET')}", flush=True)
    _candidates = [
        ROOT / "data" / "enrichment_target" / "active.jsonl",
        ROOT / "web" / "active.jsonl",
    ]
    for path in _candidates:
        if path.exists():
            print(f"[DB] loading from {path}", flush=True)
            investors = []
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        investors.append(json.loads(line))
            return investors
    # fallback 到 investors.json
    fallback = ROOT / "web" / "investors.json"
    if fallback.exists():
        print(f"[DB] loading from fallback {fallback}", flush=True)
        with open(fallback, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get("investors", [])
    raise FileNotFoundError(
        f"Investor DB not found. Tried: {_candidates} + {fallback}"
    )


# ── Public API ─────────────────────────────────────────────────────────────

def match(bp_profile: dict, top_n: int = 15, use_semantic: bool = True) -> list[dict]:
    if "_profile" not in bp_profile and "sub_sector_tags" not in bp_profile:
        raise ValueError(
            "bp_profile is missing both '_profile' and 'sub_sector_tags' — "
            "pass the output of flatten_profile(), not the raw parser dict"
        )
    investors = _load_investors()
    investors = [i for i in investors if i.get("is_real_investor") is not False]

    # 加载预计算的 investor embeddings（从磁盘，不调用 API）
    inv_vec_cache = _load_embedding_cache() if use_semantic else {}

    # 只需 embed 一次 BP narrative
    bp_vec = _build_bp_embedding(bp_profile) if use_semantic else None

    if use_semantic:
        narrative = ""
        try:
            narrative = bp_profile["_profile"]["tier3"]["narrative_text"] or ""
        except (KeyError, TypeError):
            pass
        if not narrative:
            try:
                narrative = bp_profile["tier3"]["narrative_text"] or ""
            except (KeyError, TypeError):
                pass
        if not narrative:
            narrative = bp_profile.get("narrative_text", "")
        print(f"  Tier 3 debug: narrative_text length = {len(narrative)} chars")
        print(f"  Tier 3 debug: bp_vec = {'computed' if bp_vec is not None else 'None (embedding failed)'}")
        if bp_vec is None and narrative:
            print(f"  Tier 3 debug: narrative exists but embed failed — check VOYAGE_API_KEY")
        elif not narrative:
            print(f"  Tier 3 debug: narrative_text is EMPTY — parser not extracting it")

    if bp_vec is not None:
        hits = sum(1 for inv in investors if inv.get("id") in inv_vec_cache)
        print(f"  ✓ Tier 3: vector embeddings active ({hits}/{len(investors)} investors cached)")
    else:
        print(f"  ℹ Tier 3: keyword overlap fallback")

    results = []
    for inv in investors:
        scored = _score_investor(bp_profile, inv, bp_vec, inv_vec_cache, use_semantic)
        if scored is not None:
            results.append(scored)

    # 临时调试：打印所有 investor 的 raw semantic score
    sem_scores = []
    for r in results:
        sem_scores.append(r.get('semantic_score', 0))
    if sem_scores:
        import statistics
        print(f"  Semantic score stats:")
        print(f"    min={min(sem_scores):.3f}  max={max(sem_scores):.3f}")
        print(f"    mean={statistics.mean(sem_scores):.3f}  median={statistics.median(sem_scores):.3f}")
        zeros = sum(1 for s in sem_scores if s == 0)
        print(f"    zeros={zeros}/{len(sem_scores)}")

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_n]


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile",     required=False)
    ap.add_argument("--top",         type=int, default=15)
    ap.add_argument("--no-semantic", action="store_true")
    ap.add_argument("--json",        action="store_true")
    ap.add_argument("--build-cache", action="store_true",
                    help="Pre-compute all investor embeddings and save to cache")
    args = ap.parse_args()

    if args.build_cache:
        build_investor_embedding_cache(force=True)
        return

    path = Path(args.profile)
    if not path.exists():
        sys.exit(f"ERROR: {path} not found")

    with open(path, encoding="utf-8") as f:
        bp = json.load(f)

    print("🔍 Matching…")
    results = match(bp, top_n=args.top, use_semantic=not args.no_semantic)

    if not results:
        print("⚠  No matches — check funding_stage / sector fields in BP.")
        return

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    print(f"\n{'#':<4} {'Investor':<32} {'Final':>6} {'Base':>6} {'T2mod':>6} "
          f"{'Semmod':>7} {'Status':<18}")
    print("─" * 90)
    for i, r in enumerate(results, 1):
        print(f"{i:<4} {(r['name'] or ''):<32} {r['score']:>6.3f} "
              f"{r['sub_f1_base']:>6.3f} {r['tier2_mod']:>6.3f} "
              f"{(0.85 + r['semantic_score']*0.30):>7.3f} "
              f"{r.get('sub_sector_status',''):<18}")
    print(f"\n✅ {len(results)} matched, showing top {args.top}")


if __name__ == "__main__":
    main()
