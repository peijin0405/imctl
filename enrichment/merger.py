"""Produce the final flat output record from original + validated enrichment."""

import re
from datetime import datetime, timezone
from typing import Optional


# ── display_name helpers ──────────────────────────────────────────────

_LEGAL_SUFFIXES = [
    " Management, Llc",
    " Management, LLC",
    " Management, L.L.C.",
    " Advisors, Llc",
    " Advisors, LLC",
    ", Llc",
    ", LLC",
    ", Lp",
    ", LP",
    ", L.P.",
    ", Inc.",
    ", L.L.C.",
]
_TRAILING_WORDS = [" Management", " Advisors"]

# Longest abbreviations first so e.g. "DCVC" is matched before "VC"
_ABBREVIATIONS = [
    "NVIDIA", "NYBC", "DCVC", "LMNT", "ARCH", "WAVE",
    "BMW", "DBL", "EQT", "GGV", "MFV", "MRL", "CTI",
    "JLS", "RQT", "WEX", "LDV",
    "CSC", "CVC", "IPO",
    "VC", "AI", "LP", "GP", "SV", "PE", "ML",
]

# Trigger domain-based name generation when name starts with any of these
_DESCRIPTIVE_PREFIXES = [
    "Salesforce Ventures Expands",  # longest first
    "Investment led by ",
    "Charging Towards ",
    "Purpose of ",
    "Introducing ",
    "Led by ",
    "Valuation ",
    "Why ",
]

_NULL_NAMES = {"Impact Investing"}

# Common VC name components used for compound-word splitting in domain names
_SPLIT_COMPONENTS = [
    "innovation", "ventures", "venture", "partners", "partner",
    "investments", "investment", "advisors", "advisor",
    "management", "capital", "fund", "funds", "labs", "lab",
    "movers", "health", "digital", "global", "growth",
]


def _strip_legal_suffix(name: str) -> str:
    for suffix in _LEGAL_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)].strip()
    for word in _TRAILING_WORDS:
        if name.endswith(word):
            return name[: -len(word)].strip()
    return name


def _restore_abbreviations(s: str) -> str:
    for abbr in _ABBREVIATIONS:
        s = re.sub(r'\b' + re.escape(abbr.capitalize()) + r'\b', abbr, s)
    return s


def _split_compound(s: str) -> str:
    """Insert spaces before known VC name components inside a run-together string."""
    for word in _SPLIT_COMPONENTS:
        s = re.sub(r'(?<=[a-zA-Z])(' + re.escape(word) + r')(?=[a-zA-Z]|$)',
                   r' \1', s, flags=re.IGNORECASE)
    return s


def _domain_to_name(url: str) -> Optional[str]:
    if not url:
        return None
    domain = re.sub(r'^https?://', '', url)
    domain = domain.split('/')[0].split('?')[0]
    domain = re.sub(r'^www\.', '', domain)
    domain = re.sub(r'\.[a-z]{2,10}$', '', domain)   # strip TLD
    domain = re.sub(r'\.[a-z]{2,10}$', '', domain)   # strip second level (e.g. .co.uk)
    domain = re.sub(r'[-_]', ' ', domain)             # hyphens → spaces
    domain = _split_compound(domain)
    if not domain.strip():
        return None
    result = _restore_abbreviations(domain.title())
    return result or None


def clean_display_name(name: str, official_website: str = None) -> Optional[str]:
    """Derive a clean display name from the raw firm name and optional website."""
    if not name:
        return None
    if name in _NULL_NAMES:
        return None
    for prefix in _DESCRIPTIVE_PREFIXES:
        if name.startswith(prefix):
            return _domain_to_name(official_website)
    cleaned = _strip_legal_suffix(name)
    cleaned = _split_compound(cleaned)
    cleaned = _restore_abbreviations(cleaned.title())
    return cleaned or None


# ── merge ─────────────────────────────────────────────────────────────

def merge(original: dict, enriched: dict) -> dict:
    """Only original id and name are authoritative. Everything else from enriched."""
    name = original.get("name")
    website = enriched.get("official_website")
    return {
        # Identity
        "id":           original.get("id"),
        "name":         name,
        "display_name": clean_display_name(name, website),

        # Verification
        "is_real_investor": enriched.get("is_real_investor", True),
        "official_website": website,
        "investor_type":    enriched.get("investor_type"),
        "confidence":       enriched.get("confidence"),

        # Tier 1
        "stages":             enriched.get("stages"),
        "sectors":            enriched.get("sectors"),
        "check_size_min_usd": enriched.get("check_size_min_usd"),
        "check_size_max_usd": enriched.get("check_size_max_usd"),
        "check_size_display": enriched.get("check_size_display"),
        "geo_focus":          enriched.get("geo_focus"),

        # Tier 2
        "thesis_text":                enriched.get("thesis_text"),
        "business_model_preference":  enriched.get("business_model_preference"),
        "traction_requirement":       enriched.get("traction_requirement"),
        "team_background_preference": enriched.get("team_background_preference"),
        "lead_investor":              enriched.get("lead_investor"),
        "ownership_target":           enriched.get("ownership_target"),
        "portfolio_companies":        enriched.get("portfolio_companies"),

        # Contacts
        "submission_url":   enriched.get("submission_url"),
        "general_email":    enriched.get("general_email"),
        "partner_contacts": enriched.get("partner_contacts") or [],

        # Evidence chains
        "tier1_evidence": enriched.get("tier1_evidence") or {},
        "tier2_evidence": enriched.get("tier2_evidence") or {},

        # Quality metadata
        "tier1_completeness":    enriched.get("tier1_completeness", 0),
        "tier2_completeness":    enriched.get("tier2_completeness", 0),
        "contacts_completeness": enriched.get("contacts_completeness", 0),
        "overall_confidence":    enriched.get("overall_confidence", "low"),
        "missing_fields":        enriched.get("missing_fields") or [],
        "contact_flags":         enriched.get("contact_flags") or [],
        "research_notes":        enriched.get("research_notes"),

        "enriched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
