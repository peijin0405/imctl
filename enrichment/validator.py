"""Validate and normalize v2 enrichment output from Claude."""

from typing import Optional

ALLOWED_STAGES = {
    "pre_seed", "seed", "series_a", "series_b", "series_c",
    "series_c_plus", "growth", "buyout", "pre_ipo",
    "angel", "multi_stage",
}

ALLOWED_SECTORS = {
    "ai_infra", "ai_apps", "ai_ml", "b2b_saas", "consumer",
    "fintech", "healthcare", "healthtech", "climate", "cleantech",
    "defense", "devtools", "bio", "biotech", "web3", "semiconductor",
    "hardware", "edtech", "proptech", "legaltech", "hrtech",
    "agtech", "spacetech", "energy", "robotics", "cybersecurity",
    "mobility", "media", "insurtech", "deeptech", "other",
}

ALLOWED_GEO = {
    "sf_bay_area", "nyc", "boston", "la", "seattle", "austin",
    "chicago", "miami", "us_agnostic", "global",
}

ALLOWED_INVESTOR_TYPES = {
    "vc", "pe", "angel", "micro_vc", "cvc", "family_office",
    "accelerator", "incubator", "government", "fund_of_funds",
    "hedge_fund", "other",
}

ALLOWED_LEAD        = {"lead_only", "follow_ok", "both"}
ALLOWED_BIZ_MODELS  = {
    "saas_subscription", "marketplace", "transactional",
    "usage_based", "hardware_software", "consumer_app",
    "deeptech_ip", "open_source_commercial", "services",
}
ALLOWED_TEAM_BG = {
    "ex_faang", "repeat_founder", "phd_technical",
    "domain_expert", "mba_operator", "any",
}

_BAD_THESIS = ["i cannot", "not available", "no information", "unable to find"]


def validate(raw: dict) -> tuple[bool, Optional[str], dict]:
    """
    Returns (is_valid, error_or_rejection_reason, cleaned_dict).
    is_valid=False + empty cleaned  → rejection (non-investor).
    is_valid=False + non-empty      → validation error → treat as failed.
    """
    verification = raw.get("verification") or {}
    tier1        = raw.get("tier1") or {}
    tier2        = raw.get("tier2") or {}
    contacts_raw = raw.get("contacts") or {}
    dq           = raw.get("data_quality") or {}

    # ── Rejection check ───────────────────────────────────────────────
    if verification.get("is_real_investor") is False:
        reason = verification.get("rejection_reason") or "flagged as non-investor by model"
        return False, reason, {}

    cleaned = {}

    # ── Verification ──────────────────────────────────────────────────
    cleaned["is_real_investor"] = True
    cleaned["official_website"] = _str(verification.get("official_website"))
    cleaned["investor_type"]    = _enum(verification.get("investor_type"), ALLOWED_INVESTOR_TYPES)
    cleaned["confidence"]       = _str(verification.get("confidence"))

    # ── Tier 1 ────────────────────────────────────────────────────────
    cleaned["stages"]    = _filter_list(tier1.get("stages"),    ALLOWED_STAGES)  or None
    cleaned["sectors"]   = _filter_list(tier1.get("sectors"),   ALLOWED_SECTORS) or None
    cleaned["geo_focus"] = _filter_list(tier1.get("geo_focus"), ALLOWED_GEO)     or None

    cs_min = _to_int(tier1.get("check_size_min_usd"))
    cs_max = _to_int(tier1.get("check_size_max_usd"))
    if cs_min and cs_max:
        if cs_min > cs_max:
            cs_min = cs_max = None
        elif cs_max / cs_min > 50:      # range > 50x → meaningless
            cs_min = cs_max = None
    cleaned["check_size_min_usd"] = cs_min
    cleaned["check_size_max_usd"] = cs_max
    cleaned["check_size_display"] = _fmt_check_size(cs_min, cs_max)
    cleaned["tier1_evidence"]     = tier1.get("evidence") or {}

    # ── Tier 2 ────────────────────────────────────────────────────────
    thesis = _str(tier2.get("thesis_text"))
    if thesis:
        if len(thesis) < 50:
            thesis = None
        elif any(p in thesis.lower() for p in _BAD_THESIS):
            thesis = None
    cleaned["thesis_text"] = thesis

    cleaned["business_model_preference"]  = _filter_list(tier2.get("business_model_preference"), ALLOWED_BIZ_MODELS) or None
    cleaned["team_background_preference"] = _filter_list(tier2.get("team_background_preference"), ALLOWED_TEAM_BG)   or None
    cleaned["lead_investor"]              = _enum(tier2.get("lead_investor"), ALLOWED_LEAD)

    traction = tier2.get("traction_requirement")
    cleaned["traction_requirement"] = (
        traction if isinstance(traction, dict) and any(v is not None for v in traction.values())
        else None
    )

    ownership = tier2.get("ownership_target")
    cleaned["ownership_target"] = (
        ownership if isinstance(ownership, dict) and (ownership.get("min_pct") or ownership.get("max_pct"))
        else None
    )

    raw_portfolio = tier2.get("portfolio_companies") or []
    portfolio = [
        {
            "name":                 _str(p.get("name")),
            "sector":               _enum(p.get("sector"), ALLOWED_SECTORS),
            "stage_at_investment":  _enum(p.get("stage_at_investment"), ALLOWED_STAGES),
            "year":                 _to_int(p.get("year")),
            "founder_background":   _str(p.get("founder_background")),
        }
        for p in (raw_portfolio if isinstance(raw_portfolio, list) else [])
        if isinstance(p, dict) and p.get("name")
    ]
    cleaned["portfolio_companies"] = portfolio or None
    cleaned["tier2_evidence"]      = tier2.get("evidence") or {}

    # ── Contacts ──────────────────────────────────────────────────────
    contacts, contact_flags = validate_contacts(contacts_raw)
    cleaned["submission_url"]    = contacts.get("submission_url")
    cleaned["general_email"]     = contacts.get("general_email")
    cleaned["partner_contacts"]  = contacts.get("partner_contacts") or []
    cleaned["contact_flags"]     = contact_flags

    # ── Completeness scores ───────────────────────────────────────────
    # tier1: stages(25) + sectors(25) + check_size(25) + geo(25) = 100
    t1 = (
        (25 if cleaned["stages"]    else 0) +
        (25 if cleaned["sectors"]   else 0) +
        (25 if cs_min or cs_max     else 0) +
        (25 if cleaned["geo_focus"] else 0)
    )

    # tier2: thesis≥200w(35) + biz(15) + traction(15) + lead(20) + portfolio≥5(15) = 100
    thesis_words = len(cleaned["thesis_text"].split()) if cleaned["thesis_text"] else 0
    t2 = (
        (35 if thesis_words >= 200                                      else 0) +
        (15 if cleaned["business_model_preference"]                     else 0) +
        (15 if cleaned["traction_requirement"]                          else 0) +
        (20 if cleaned["lead_investor"]                                 else 0) +
        (15 if portfolio and len(portfolio) >= 5                        else 0)
    )

    # contacts: sub_url(30) + 1st LinkedIn(40) + 2nd LinkedIn bonus(20) + email(10) = 100
    ct = _compute_contacts_completeness(contacts)

    cleaned["tier1_completeness"]    = t1
    cleaned["tier2_completeness"]    = t2
    cleaned["contacts_completeness"] = ct

    if t1 >= 75 and cleaned["tier1_evidence"]:
        cleaned["overall_confidence"] = "high"
    elif t1 >= 50:
        cleaned["overall_confidence"] = "medium"
    else:
        cleaned["overall_confidence"] = "low"

    missing = [
        f for f, v in [
            ("stages",              cleaned["stages"]),
            ("sectors",             cleaned["sectors"]),
            ("check_size",          cs_min or cs_max),
            ("geo_focus",           cleaned["geo_focus"]),
            ("thesis_text",         cleaned["thesis_text"]),
            ("lead_investor",       cleaned["lead_investor"]),
            ("business_model",      cleaned["business_model_preference"]),
            ("traction",            cleaned["traction_requirement"]),
            ("submission_url",      cleaned["submission_url"]),
            ("partner_contacts",    cleaned["partner_contacts"] or None),
        ]
        if not v
    ]
    cleaned["missing_fields"]  = missing
    cleaned["research_notes"]  = _str(dq.get("research_notes"))

    return True, None, cleaned


def is_placeholder_name(name: str) -> bool:
    """Return True if name is a descriptive placeholder rather than a real person's name."""
    if not name:
        return True
    n = name.lower().strip()
    # Contains parentheses (e.g. "Partner (Name)")
    if "(" in name:
        return True
    # Starts with a job title instead of a real name
    if n.startswith(("managing partner", "general partner", "founding partner",
                     "senior partner", "venture partner")):
        return True
    # Generic placeholder patterns
    _PLACEHOLDER_FRAGMENTS = [
        "founder partner", "engineer partner", "investor partner",
        "partner 1", "partner 2", "partner 3",
        "partner one", "partner two", "partner three",
        "name 1", "name 2", "co-founder partner",
    ]
    return any(frag in n for frag in _PLACEHOLDER_FRAGMENTS)


def validate_contacts(contacts: dict) -> tuple[dict, list]:
    """Validate and clean the contacts block. Returns (cleaned_contacts, flags)."""
    flags    = []
    contacts = dict(contacts)  # shallow copy

    # general_email — check domain looks real
    email = contacts.get("general_email") or ""
    if email:
        if not any(d in email for d in [".com", ".vc", ".fund", ".io", ".co"]):
            flags.append("invalid_email_format")
            contacts["general_email"] = None
        elif "@" not in email:
            flags.append("invalid_email_no_at")
            contacts["general_email"] = None

    # partner_contacts
    raw_partners = contacts.get("partner_contacts")
    partners = []
    if isinstance(raw_partners, list):
        for p in raw_partners:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            name = _str(p.get("name")) or ""
            # Drop placeholder names
            if is_placeholder_name(name):
                flags.append(f"placeholder_partner_name:{name}")
                continue
            li = _str(p.get("linkedin_url"))
            if li and "linkedin.com/in/" not in li:
                flags.append(f"invalid_linkedin:{name}")
                li = None
            email_p = _str(p.get("email"))
            if email_p and "@" not in email_p:
                email_p = None
            partners.append({
                "name":         name,
                "title":        _str(p.get("title")),
                "linkedin_url": li,
                "email":        email_p,
                "twitter_x":    _str(p.get("twitter_x")),
            })
    contacts["partner_contacts"] = partners

    # submission_url — must be a clean URL
    sub = contacts.get("submission_url") or ""
    if sub and not sub.startswith("http"):
        flags.append("invalid_submission_url")
        contacts["submission_url"] = None
    elif sub and " " in sub:
        flags.append("dirty_submission_url")
        contacts["submission_url"] = None

    return contacts, flags


def _compute_contacts_completeness(contacts: dict) -> int:
    score = 0
    if contacts.get("submission_url"):
        score += 30
    li_count = sum(
        1 for p in (contacts.get("partner_contacts") or [])
        if p.get("linkedin_url")
    )
    if li_count >= 1:
        score += 40
    if li_count >= 2:
        score += 20
    if contacts.get("general_email"):
        score += 10
    return min(score, 100)


# ── Helpers ───────────────────────────────────────────────────────────

def _str(val) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _enum(val, allowed: set) -> Optional[str]:
    s = _str(val)
    return s if s in allowed else None


def _filter_list(val, allowed: set) -> list:
    if val is None:
        return []
    if isinstance(val, str):
        val = [val]
    return [v for v in val if isinstance(v, str) and v in allowed]


def _to_int(val) -> Optional[int]:
    if val is None:
        return None
    try:
        n = int(float(str(val).replace(",", "").replace("$", "")))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _fmt_check_size(cs_min: Optional[int], cs_max: Optional[int]) -> Optional[str]:
    if cs_min and cs_max:
        return f"{_abbrev(cs_min)}–{_abbrev(cs_max)}"
    if cs_min:
        return f"{_abbrev(cs_min)}+"
    if cs_max:
        return f"up to {_abbrev(cs_max)}"
    return None


def _abbrev(n: int) -> str:
    if n >= 1_000_000_000:
        return f"${n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"${v:.0f}M" if v == int(v) else f"${v:.1f}M"
    if n >= 1_000:
        v = n / 1_000
        return f"${v:.0f}K" if v == int(v) else f"${v:.1f}K"
    return f"${n}"
