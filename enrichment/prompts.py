"""Prompts for v2 enrichment pipeline — with contacts."""

SYSTEM_PROMPT = """You are an expert research analyst specializing in venture capital and private equity. Your job is to research investment firms and extract structured data accurately.

You have web_search and web_fetch tools. Use them efficiently.

Research protocol — strict 5-call budget:

Call 1 — Search: "{firm_name} venture capital"
  → Confirm official website URL only. Do not fetch yet.

Call 2 — Fetch: confirmed homepage only
  → Extract everything visible: thesis_text, stages, sectors, geo,
    investor_type, check_size, ownership_target, traction clues,
    partner names, general_email, submission_url.
  → Note any real subpage links (/about /portfolio /team) that exist.

Call 3 — Fetch: ONE subpage only (choose the most valuable)
  → If stages/sectors unclear: fetch /portfolio
  → Otherwise: fetch /about or /team
  → Extract: portfolio companies, thesis supplement, partner details.

Call 4 — Search: "{partner1_name} {partner2_name} {firm_name} LinkedIn apply pitch"
  → Combine partner LinkedIn + submission URL into one search query.
  → Use top 2 partner names found in Call 2/3.

Call 5 — Only if critical gaps remain (check_size OR thesis_text OR submission_url missing)
  → One targeted search to fill the single most important gap.
  → STOP after this. Write JSON output immediately.

HARD RULE: Never fetch a URL you haven't confirmed exists.
Never do separate searches for things you can combine.
Null is always better than a 6th tool call.

Never fill a field without evidence. Never guess."""


_USER_PROMPT_TEMPLATE = """Research this investment firm completely.
The firm name is the ONLY reliable field — treat everything else as an unverified hint for your searches.

FIRM NAME: __NAME__
Search hints (unverified):
  website: __WEBSITE__
  type: __INVESTOR_TYPE__
  region: __REGION__

══════════════════════════════════════════════════
STEP 1 — IDENTITY VERIFICATION
══════════════════════════════════════════════════

Is this a real, active investment institution that deploys capital into startups/companies?

INCLUDE: VC, PE, angel fund, family office, CVC, accelerator,
         government investment fund, university endowment (if it actively invests externally)

EXCLUDE: law firms, pure charities (no investment mandate),
         consulting firms, media companies, banks (unless dedicated VC/PE arm),
         portfolio companies accidentally listed as investors,
         individual venture partners listed as if they were a firm

══════════════════════════════════════════════════
STEP 2 — TIER 1: HARD FILTER FIELDS
══════════════════════════════════════════════════
These fields are used to filter out non-matching investors.
Research each one from actual sources. Include evidence quotes.

[ stages ]
What investment stages does this firm focus on?
Sources to check: their website, Crunchbase, Tracxn, actual portfolio company stages
MUST use ONLY these exact lowercase values:
"pre_seed" "seed" "series_a" "series_b" "series_c"
"series_c_plus" "growth" "buyout" "pre_ipo" "angel" "multi_stage"
Never use: Pre-A, Late Stage, Early Stage, Angel (capitalized)

[ sectors ]
What industries does this firm invest in?
Sources: thesis page, portfolio companies, explicit focus statements
MUST use ONLY these exact values:
"ai_infra" "ai_apps" "ai_ml" "b2b_saas" "consumer" "fintech"
"healthcare" "healthtech" "climate" "cleantech" "defense"
"devtools" "bio" "biotech" "web3" "semiconductor" "hardware"
"edtech" "proptech" "legaltech" "hrtech" "agtech" "spacetech"
"energy" "robotics" "cybersecurity" "mobility" "media"
"insurtech" "deeptech" "other"

[ check_size_min_usd ] and [ check_size_max_usd ]
What is the typical single investment amount?
Search for: "check size" "ticket size" "investment size"
            "we invest $X" "typical investment"
Convert to integers: $500K->500000, $2M->2000000, $1B->1000000000
If range is wider than 50x (e.g. $100K-$50M), set both to null
— a range that wide is meaningless for filtering.

[ geo_focus ]
Where does this firm primarily invest geographically?
MUST use ONLY these exact values (can be multiple):
"sf_bay_area" "nyc" "boston" "la" "seattle" "austin"
"chicago" "miami" "us_agnostic" "global"

[ investor_type ]
MUST use ONLY one of these exact values:
"vc" "pe" "angel" "micro_vc" "cvc" "family_office"
"accelerator" "incubator" "government" "fund_of_funds"
"hedge_fund" "other"

══════════════════════════════════════════════════
STEP 3 — TIER 2: SOFT MATCH FIELDS
══════════════════════════════════════════════════
These fields improve ranking quality. Get as many as possible.

[ thesis_text ] <- HIGHEST PRIORITY TIER 2 FIELD
Copy the firm's investment philosophy/thesis VERBATIM from their website.
Do not summarize — copy the actual text.
Target: 200-800 words. More is better than less.
Sources: /about, /thesis, /approach, /strategy, /manifesto
Combine multiple sections if needed to reach 200+ words.
If their site has very little text, include what exists + supplement with quotes from interviews or blog posts.

[ business_model_preference ]
What business models does this firm prefer?
Infer from portfolio if not explicitly stated.
Valid values (select all that apply):
"saas_subscription" "marketplace" "transactional" "usage_based"
"hardware_software" "consumer_app" "deeptech_ip"
"open_source_commercial" "services"

[ traction_requirement ]
What revenue/traction is required before they invest?
Format: {"pre_revenue_ok": true/false/null, "min_arr_usd": integer or null, "notes": "exact quote from their site or a source"}
Look for: "pre-revenue" "must have X customers" "$XM ARR minimum"
          "product-market fit" "early traction"

[ team_background_preference ]
What founder profiles does this firm typically back?
Infer from portfolio founders if not explicitly stated.
Valid values (select all that apply):
"ex_faang" "repeat_founder" "phd_technical"
"domain_expert" "mba_operator" "any"

[ lead_investor ]
Does this firm lead rounds or follow others?
MUST use ONE of: "lead_only" "follow_ok" "both"
Look for: "we lead" "we co-invest" "lead or follow"
          "we write the first check"

[ ownership_target ]
What equity stake does this firm target?
Format: {"min_pct": float, "max_pct": float}
Look for: "X% ownership" "10-15% stake" "meaningful ownership"

[ portfolio_companies ]
List up to 10 actual portfolio companies.
For each, try to note the founder background if findable.
Format: [{"name": "Company Name", "sector": "exact sector value", "stage_at_investment": "exact stage value", "year": integer or null, "founder_background": "ex-Google / PhD MIT / repeat founder / etc or null"}]

══════════════════════════════════════════════════
STEP 4 — CONTACT INFORMATION
══════════════════════════════════════════════════
This is critical for product usability. Research thoroughly.

[ submission_url ]
Does this firm have an official page where founders can submit their pitch/deck?
Search: "__NAME__ apply" "__NAME__ pitch"
        "__NAME__ submit startup" "__NAME__ contact founders"
Examples: felicis.com/apply, sequoiacap.com/companies/pitch
Include the full URL if found, null if not.

[ partner_contacts ]
Find the TOP 2-3 INVESTING PARTNERS (GP / General Partner /
Managing Partner / Partner level — NOT Associates or Analysts).

For each partner, find:
1. LinkedIn URL — search "{partner_name} __NAME__ LinkedIn"
2. Email — ONLY if publicly listed on firm website or their
   personal site. Do NOT guess or construct email addresses.
   Common public formats to look for: on /team page,
   personal websites, Twitter/X bio

Format: [{"name": "Full Name", "title": "General Partner", "linkedin_url": "https://linkedin.com/in/... or null", "email": "only if publicly listed, else null", "twitter_x": "@handle or null"}]

[ general_contact ]
General firm email if listed on website (info@, hello@, etc.)
Only include if explicitly listed — do not guess.

══════════════════════════════════════════════════
OUTPUT — Return ONLY valid JSON, no explanation:
══════════════════════════════════════════════════

{
  "firm_id": "__FIRM_ID__",
  "firm_name": "__NAME__",

  "verification": {
    "is_real_investor": true/false,
    "investor_type": "exact value from list",
    "confidence": "high/medium/low",
    "official_website": "confirmed URL or null",
    "rejection_reason": null
  },

  "tier1": {
    "stages": [] or null,
    "sectors": [] or null,
    "check_size_min_usd": integer or null,
    "check_size_max_usd": integer or null,
    "check_size_display": "$XM-$YM" or null,
    "geo_focus": [] or null,
    "evidence": {
      "stages": "exact quote or source URL",
      "sectors": "exact quote or source URL",
      "check_size": "exact quote or source URL",
      "geo": "exact quote or source URL"
    }
  },

  "tier2": {
    "thesis_text": "200-800 word verbatim text" or null,
    "business_model_preference": [] or null,
    "traction_requirement": {...} or null,
    "team_background_preference": [] or null,
    "lead_investor": "value" or null,
    "ownership_target": {...} or null,
    "portfolio_companies": [] or null,
    "evidence": {
      "thesis_source_url": "URL",
      "traction_source": "exact quote",
      "lead_source": "exact quote"
    }
  },

  "contacts": {
    "submission_url": "URL or null",
    "general_email": "email or null",
    "partner_contacts": [
      {
        "name": "Full Name",
        "title": "GP title",
        "linkedin_url": "URL or null",
        "email": "only if public, else null",
        "twitter_x": "@handle or null"
      }
    ]
  },

  "data_quality": {
    "tier1_completeness": 0-100,
    "tier2_completeness": 0-100,
    "contacts_completeness": 0-100,
    "overall_confidence": "high/medium/low",
    "missing_fields": [],
    "research_notes": "any caveats"
  }
}

SCORING RULES:
tier1_completeness:
  stages filled:     +25
  sectors filled:    +25
  check_size filled: +25
  geo filled:        +25

tier2_completeness:
  thesis_text >= 200 words: +35
  business_model filled:    +15
  traction filled:          +15
  lead_investor filled:     +20
  portfolio >= 5 companies: +15

contacts_completeness:
  submission_url found:        +30
  >= 1 partner LinkedIn found: +40
  >= 2 partner LinkedIns found: +20 (bonus)
  general_email found:         +10

overall_confidence = "high" requires:
  tier1_completeness >= 75 AND all filled fields have evidence"""


def build_user_prompt(record: dict) -> str:
    firm_id = str(record.get("id") or "")
    name    = str(record.get("name") or "")
    website = str(record.get("website") or "unknown")
    itype   = str(record.get("investor_type") or "unknown")
    region  = str(record.get("region") or record.get("hq_country") or "unknown")

    return (
        _USER_PROMPT_TEMPLATE
        .replace("__FIRM_ID__", firm_id)
        .replace("__NAME__", name)
        .replace("__WEBSITE__", website)
        .replace("__INVESTOR_TYPE__", itype)
        .replace("__REGION__", region)
    )
