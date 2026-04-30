import os
import json
import re
import time
import logging
from collections import defaultdict
from datetime import datetime
from dotenv import load_dotenv
from exa_py import Exa

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

EXA_API_KEY = os.getenv("EXA_API_KEY")
DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "investors.json")
WEB_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "web", "investors.json")

# ── Domain lists ──────────────────────────────────────────────────────────────

FIRM_DOMAINS = [
    # General VC data aggregators
    "crunchbase.com",
    "pitchbook.com",
    "dealroom.co",
    "tracxn.com",
    "linkedin.com",
    # Healthcare / Life Sciences
    "rockhealth.com",
    "7wireventures.com",
    "healthcareventure.com",
    "versantventures.com",
    "atlasventure.com",
    "orbimed.com",
    "gv.com",
    "fiercebiotech.com",
    "medcitynews.com",
    "statnews.com",
    # AI / General VC
    "a16z.com",
    "sequoiacap.com",
    "khoslaventures.com",
    "generalatlantic.com",
    "lightspeedvp.com",
    "greylock.com",
    "accel.com",
    "nea.com",
    "radical.vc",
    "coatue.com",
    # Semiconductor
    "semi.org",
    "icinsights.com",
    "semiengineering.com",
    "walden.vc",
    # Energy / CleanTech
    "breakthrough.energy",
    "lowercarbon.cc",
    "energyimpactvp.com",
    "cleantech.com",
    "bnef.com",
    "woodmac.com",
    # FinTech
    "ribbitcap.com",
    "qed-investors.com",
    "fintechfutures.com",
    "cbinsights.com",
    # EdTech
    "gsvventures.com",
    "reach.capital",
    "owl.vc",
    "holoniq.com",
]

# Known sector label prefixes — order matters (longer prefixes first to avoid partial matches)
SECTOR_PREFIXES = [
    "ai_apps", "ai_hardware", "semiconductor",
    "healthcare", "edtech", "fintech", "greentech", "energy",
]


# ── Client ────────────────────────────────────────────────────────────────────

def get_exa_client() -> Exa:
    if not EXA_API_KEY:
        raise ValueError("EXA_API_KEY not set in environment")
    return Exa(api_key=EXA_API_KEY)


# ── Search configs ────────────────────────────────────────────────────────────

SEARCH_CONFIGS = [
    # ── Legacy health-AI queries (preserved) ─────────────────────────────────
    {
        "query": "health AI venture capital firm official website portfolio investments",
        "type": "neural",
        "num_results": 20,
        "include_domains": ["crunchbase.com", "pitchbook.com"],
        "label": "vc_crunchbase",
    },
    {
        "query": "digital health AI venture capital firm investment thesis portfolio",
        "type": "neural",
        "num_results": 20,
        "label": "vc_firms",
    },
    {
        "query": "healthcare AI clinical technology venture capital fund investment focus",
        "type": "neural",
        "num_results": 20,
        "label": "health_ai_vc",
    },
    {
        "query": "AI drug discovery biotech venture capital fund portfolio companies",
        "type": "neural",
        "num_results": 20,
        "label": "drug_discovery_vc",
    },
    {
        "query": "mental health telehealth digital therapeutics venture capital firm",
        "type": "neural",
        "num_results": 20,
        "label": "mental_health_vc",
    },

    # ── ai_apps: AI Applications, Enterprise SaaS, AI Infrastructure ─────────
    {
        "query": "enterprise AI SaaS venture capital investment firm portfolio series A",
        "type": "neural",
        "num_results": 15,
        "label": "ai_apps_enterprise",
    },
    {
        "query": "AI agent autonomous workflow startup venture capital fund portfolio",
        "type": "neural",
        "num_results": 15,
        "label": "ai_apps_agents",
    },
    {
        "query": "generative AI foundation model LLM startup venture capital investment",
        "type": "neural",
        "num_results": 15,
        "label": "ai_apps_generative",
    },
    {
        "query": "AI infrastructure MLOps vector database startup venture fund portfolio",
        "type": "neural",
        "num_results": 15,
        "label": "ai_apps_infra",
    },
    {
        "query": "vertical industry AI software startup venture capital series A B",
        "type": "neural",
        "num_results": 15,
        "label": "ai_apps_vertical",
    },
    {
        "query": "computer vision multimodal AI startup venture capital investment thesis",
        "type": "neural",
        "num_results": 15,
        "label": "ai_apps_cv",
    },
    {
        "query": "AI copilot developer tools productivity startup venture fund",
        "type": "neural",
        "num_results": 15,
        "label": "ai_apps_devtools",
    },
    {
        "query": "AI data analytics platform startup venture capital portfolio companies",
        "type": "neural",
        "num_results": 15,
        "label": "ai_apps_analytics",
    },

    # ── ai_hardware: Robotics, Humanoid, Edge AI, Autonomous Systems ──────────
    {
        "query": "robotics embodied intelligence startup venture capital investment fund",
        "type": "neural",
        "num_results": 15,
        "label": "ai_hardware_robotics",
    },
    {
        "query": "humanoid robot physical AI startup venture capital series A funding",
        "type": "neural",
        "num_results": 15,
        "label": "ai_hardware_humanoid",
    },
    {
        "query": "autonomous vehicle self-driving AI startup venture fund portfolio",
        "type": "neural",
        "num_results": 15,
        "label": "ai_hardware_auto",
    },
    {
        "query": "industrial automation edge AI manufacturing startup venture capital",
        "type": "neural",
        "num_results": 15,
        "label": "ai_hardware_industrial",
    },
    {
        "query": "drone unmanned aerial vehicle UAV startup venture capital investment",
        "type": "neural",
        "num_results": 15,
        "label": "ai_hardware_drone",
    },
    {
        "query": "AI chip GPU NPU hardware accelerator startup venture capital fund",
        "type": "neural",
        "num_results": 15,
        "label": "ai_hardware_chips",
    },
    {
        "query": "robot perception LIDAR sensor fusion AI startup investor portfolio",
        "type": "neural",
        "num_results": 15,
        "label": "ai_hardware_sensing",
    },

    # ── semiconductor: Chip Design, AI Chip, Packaging, EDA, Equipment ────────
    {
        "query": "semiconductor chip design fabless startup venture capital investment",
        "type": "neural",
        "num_results": 15,
        "label": "semiconductor_chip_design",
    },
    {
        "query": "AI chip GPU NPU neural processor startup venture fund portfolio",
        "type": "neural",
        "num_results": 15,
        "label": "semiconductor_ai_chip",
    },
    {
        "query": "power semiconductor SiC GaN wide bandgap startup venture capital",
        "type": "neural",
        "num_results": 15,
        "label": "semiconductor_power",
    },
    {
        "query": "semiconductor equipment wafer fabrication process startup venture capital",
        "type": "neural",
        "num_results": 15,
        "label": "semiconductor_equipment",
    },
    {
        "query": "EDA chip design tools IP core startup venture fund investment",
        "type": "neural",
        "num_results": 15,
        "label": "semiconductor_eda",
    },
    {
        "query": "advanced packaging chiplet heterogeneous integration startup VC",
        "type": "neural",
        "num_results": 15,
        "label": "semiconductor_packaging",
    },
    {
        "query": "MEMS sensor semiconductor materials startup venture capital funding",
        "type": "neural",
        "num_results": 15,
        "label": "semiconductor_mems",
    },
    {
        "query": "RF millimeter wave 5G 6G chip startup venture capital funding",
        "type": "neural",
        "num_results": 15,
        "label": "semiconductor_rf",
    },

    # ── healthcare: Drug, AI Pharma, Gene Therapy, MedTech, Digital Health ────
    {
        "query": "biotech novel drug discovery series A venture capital startup",
        "type": "neural",
        "num_results": 15,
        "label": "healthcare_drug",
    },
    {
        "query": "AI drug discovery computational biology startup venture fund",
        "type": "neural",
        "num_results": 15,
        "label": "healthcare_ai_pharma",
    },
    {
        "query": "cell gene therapy CAR-T gene editing startup venture capital",
        "type": "neural",
        "num_results": 15,
        "label": "healthcare_gene",
    },
    {
        "query": "medical device MedTech surgical robot startup venture capital",
        "type": "neural",
        "num_results": 15,
        "label": "healthcare_device",
    },
    {
        "query": "digital therapeutics DTx chronic disease management startup VC",
        "type": "neural",
        "num_results": 15,
        "label": "healthcare_dtx",
    },
    {
        "query": "precision medicine genomics liquid biopsy diagnostic startup venture fund",
        "type": "neural",
        "num_results": 15,
        "label": "healthcare_genomics",
    },
    {
        "query": "consumer health wellness mental health wearable startup venture capital",
        "type": "neural",
        "num_results": 15,
        "label": "healthcare_consumer",
    },
    {
        "query": "medical imaging radiology pathology AI startup venture capital investment",
        "type": "neural",
        "num_results": 15,
        "label": "healthcare_imaging",
    },

    # ── edtech: AI Education, Vocational, K-12, Corporate, Higher Ed ──────────
    {
        "query": "AI tutoring personalized adaptive learning startup venture capital",
        "type": "neural",
        "num_results": 15,
        "label": "edtech_ai_tutoring",
    },
    {
        "query": "K-12 online education platform startup venture fund series A",
        "type": "neural",
        "num_results": 15,
        "label": "edtech_k12",
    },
    {
        "query": "vocational skills training coding bootcamp startup venture capital",
        "type": "neural",
        "num_results": 15,
        "label": "edtech_vocational",
    },
    {
        "query": "corporate learning LMS workforce development startup venture fund",
        "type": "neural",
        "num_results": 15,
        "label": "edtech_corporate",
    },
    {
        "query": "higher education university technology MOOC startup venture capital",
        "type": "neural",
        "num_results": 15,
        "label": "edtech_higher_ed",
    },
    {
        "query": "language learning AI edtech startup venture fund portfolio",
        "type": "neural",
        "num_results": 15,
        "label": "edtech_language",
    },
    {
        "query": "STEM coding robotics education startup venture capital investment",
        "type": "neural",
        "num_results": 15,
        "label": "edtech_stem",
    },

    # ── fintech: Payments, Lending, Quant, Crypto, InsurTech, RegTech ─────────
    {
        "query": "payments digital banking neobank fintech startup venture capital",
        "type": "neural",
        "num_results": 15,
        "label": "fintech_payments",
    },
    {
        "query": "AI credit risk lending BNPL fintech startup venture fund portfolio",
        "type": "neural",
        "num_results": 15,
        "label": "fintech_lending",
    },
    {
        "query": "quantitative trading algorithmic finance AI startup venture capital",
        "type": "neural",
        "num_results": 15,
        "label": "fintech_quant",
    },
    {
        "query": "blockchain crypto DeFi web3 digital asset venture capital firm",
        "type": "neural",
        "num_results": 15,
        "label": "fintech_crypto",
    },
    {
        "query": "insurtech insurance technology parametric startup venture fund",
        "type": "neural",
        "num_results": 15,
        "label": "fintech_insurtech",
    },
    {
        "query": "regtech compliance AML KYC financial crime startup venture capital",
        "type": "neural",
        "num_results": 15,
        "label": "fintech_regtech",
    },
    {
        "query": "embedded finance open banking banking-as-a-service startup VC",
        "type": "neural",
        "num_results": 15,
        "label": "fintech_embedded",
    },
    {
        "query": "wealth management robo-advisor wealthtech startup venture capital",
        "type": "neural",
        "num_results": 15,
        "label": "fintech_wealth",
    },

    # ── greentech: Carbon Capture, Green Chemistry, Materials, Circular ────────
    {
        "query": "advanced materials nanotechnology composite startup venture capital",
        "type": "neural",
        "num_results": 15,
        "label": "greentech_materials",
    },
    {
        "query": "sustainable green chemistry bio-based materials startup venture fund",
        "type": "neural",
        "num_results": 15,
        "label": "greentech_chemistry",
    },
    {
        "query": "carbon capture CCUS direct air capture startup venture capital",
        "type": "neural",
        "num_results": 15,
        "label": "greentech_ccus",
    },
    {
        "query": "circular economy recycling plastic waste upcycling startup venture capital",
        "type": "neural",
        "num_results": 15,
        "label": "greentech_circular",
    },
    {
        "query": "water treatment purification environmental technology startup VC",
        "type": "neural",
        "num_results": 15,
        "label": "greentech_water",
    },
    {
        "query": "specialty chemicals functional coatings industrial materials startup VC",
        "type": "neural",
        "num_results": 15,
        "label": "greentech_specialty",
    },
    {
        "query": "bioplastics bio-based polymer sustainable packaging startup venture fund",
        "type": "neural",
        "num_results": 15,
        "label": "greentech_bioplastics",
    },

    # ── energy: Solar, Storage, Hydrogen, Wind, Nuclear SMR, Grid ─────────────
    {
        "query": "solar energy photovoltaic perovskite startup venture capital fund",
        "type": "neural",
        "num_results": 15,
        "label": "energy_solar",
    },
    {
        "query": "battery energy storage grid-scale BESS startup venture capital",
        "type": "neural",
        "num_results": 15,
        "label": "energy_storage",
    },
    {
        "query": "green hydrogen electrolysis fuel cell startup venture capital",
        "type": "neural",
        "num_results": 15,
        "label": "energy_hydrogen",
    },
    {
        "query": "wind energy offshore onshore renewable startup venture fund",
        "type": "neural",
        "num_results": 15,
        "label": "energy_wind",
    },
    {
        "query": "nuclear small modular reactor SMR fusion energy startup VC",
        "type": "neural",
        "num_results": 15,
        "label": "energy_nuclear",
    },
    {
        "query": "smart grid demand response virtual power plant startup venture capital",
        "type": "neural",
        "num_results": 15,
        "label": "energy_grid",
    },
    {
        "query": "EV charging electric vehicle infrastructure startup venture fund",
        "type": "neural",
        "num_results": 15,
        "label": "energy_ev",
    },
    {
        "query": "climate tech energy transition deep tech startup venture capital",
        "type": "neural",
        "num_results": 15,
        "label": "energy_climate",
    },
]


def run_search(exa: Exa, config: dict) -> list:
    kwargs = {k: v for k, v in config.items() if k != "label"}
    query = kwargs.pop("query")
    try:
        result = exa.search_and_contents(
            query,
            text={"max_characters": 5000},
            **kwargs,
        )
        return result.results
    except Exception as exc:
        log.warning("Search '%s' failed: %s", config.get("label"), exc)
        return []


# ── Validation ────────────────────────────────────────────────────────────────

HEADLINE_PATTERNS = re.compile(
    r"\b(raises?|raised|raising|funding|the round|want to|wants to|launches?|"
    r"announces?|announced|acquires?|acquired|closes?|closed|secures?|secured|"
    r"image credits?|getty)\b",
    re.IGNORECASE,
)

PROFILE_SUFFIX_RE = re.compile(
    r"\s*[-–|]\s*(crunchbase|pitchbook).*$"
    r"|\s+(investor profile|profile\s*&\s*investments?|investment portfolio)$",
    re.IGNORECASE,
)

GENERIC_NAMES = {
    "about", "home", "homepage", "portfolio", "ventures", "capital",
    "partners", "fund", "investments", "team", "contact", "news",
    "blog", "people", "health", "bio", "labs", "group",
    "managing partner", "founding partner", "general partner",
    "venture capital", "private equity",
    "early stage venture", "late stage venture", "growth stage venture",
    "limited partners", "general partners",
    "angel investors", "angel investor",
}

TAGLINE_RE = re.compile(
    r"\b(investing in|driving|pioneering|building|accessing|platform|"
    r"innovation|future of|centered|behavioral|wellness|digital health\b.*\b)\b",
    re.IGNORECASE,
)

NAV_PREFIX_RE = re.compile(
    r"^(?:about|home|homepage|portfolio|team|our current ventures?|read)\s*(?:[-–|:\s])\s*",
    re.IGNORECASE,
)
NAV_SUFFIX_RE = re.compile(
    r"\s*(?:[-–|:\s])?\s*(?:home|homepage|portfolio)$",
    re.IGNORECASE,
)


def clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip()
    name = PROFILE_SUFFIX_RE.sub("", name).strip()
    name = NAV_PREFIX_RE.sub("", name).strip()
    name = NAV_SUFFIX_RE.sub("", name).strip()
    name = re.sub(r"\s+Home\s*[-–|].*$", "", name, flags=re.IGNORECASE).strip()
    words = name.split()
    half = len(words) // 2
    if half >= 2 and words[:half] == words[half:]:
        name = " ".join(words[:half])
    return name


def is_valid_firm_name(name: str) -> bool:
    if not name or len(name) < 4:
        return False
    name = re.sub(r"\s+", " ", name).strip()
    if HEADLINE_PATTERNS.search(name):
        return False
    if re.search(r"(crunchbase|pitchbook|investor profile)", name, re.IGNORECASE):
        return False
    if name.lower().rstrip("s") in {g.rstrip("s") for g in GENERIC_NAMES}:
        return False
    if TAGLINE_RE.search(name):
        return False
    if re.search(
        r"^(she |he |they |our |read |core areas|people\.|venture partner|founding partner|managing partner)",
        name, re.IGNORECASE,
    ):
        return False
    if len(name.split()) > 6:
        return False
    if not (name[0].isupper() or name[0].isdigit()):
        return False
    return True


# ── Field extraction ──────────────────────────────────────────────────────────

# Four-tier stage regex.  Alternation order is critical:
#   pre[-\s]?seed / preseed must appear before bare `seed` to win at the same position.
#   pre[-\s]?a must appear before `seed` (different prefix, no overlap).
STAGE_RE = re.compile(
    r"\b("
    r"angel|pre[-\s]?seed|preseed"                                               # Tier 1 · Angel
    r"|pre[-\s]?a|seed"                                                          # Tier 2 · Pre-A
    r"|series\s+[abc]"                                                           # Tier 3 · Series A/B/C
    r"|series\s+[d-z]|late[-\s]?stage|pre[-\s]?ipo|growth\s+equity|growth\s+round"  # Tier 4 · Pre-IPO
    r")\b",
    re.IGNORECASE,
)

FIRM_RE = re.compile(
    r"\b([A-Z][a-zA-Z0-9&\-\s]{2,40}"
    r"(?:Ventures?|Capital|Partners?|Fund|Health|Investments?|Equity|VC|Bio|Labs?|"
    r"Biotech|Therapeutics|Sciences|Group|Asset Management|Advisors?))\b"
)

REGION_MAP = [
    (["new york", "nyc", "new york city", "manhattan"],       "North America"),
    (["san francisco", "silicon valley", "bay area", "palo alto",
      "menlo park", "boston", "cambridge", "seattle", "austin",
      "los angeles", "chicago", "toronto", " ca,", " ca ", "united states", " us,", " usa"],
                                                               "North America"),
    (["beijing", "shanghai", "china", "shenzhen"],            "China"),
    (["london", "united kingdom", " uk,", " uk ", "europe",
      "berlin", "paris", "zurich", "amsterdam", "stockholm"], "Europe"),
    (["singapore", "asia pacific", "hong kong", "tokyo", "seoul"], "Asia Pacific"),
    (["dubai", "uae", "middle east", "saudi", "riyadh"],      "Middle East"),
]

# Per-sector keyword lists used for both FOCUS_KEYWORDS and sector inference.
# Order within each list does not matter; dict insertion order defines sector priority tie-breaks.
SECTOR_KEYWORD_MAP: dict[str, list[str]] = {
    "ai_apps": [
        "artificial intelligence", "machine learning", "large language model",
        "generative AI", "LLM", "AI agent", "enterprise AI", "AI SaaS",
        "MLOps", "vector database", "AI infrastructure", "foundation model",
        "natural language processing", "AI copilot", "multimodal AI",
    ],
    "ai_hardware": [
        "robotics", "embodied intelligence", "humanoid robot", "autonomous vehicle",
        "self-driving", "edge AI", "industrial automation",
        "neural processing unit", "NPU", "robot perception", "mechanical arm",
        "autonomous systems", "LIDAR", "drone", "embedded AI",
    ],
    "semiconductor": [
        "semiconductor", "chip design", "fabless", "EDA", "FPGA",
        "GPU", "power semiconductor", "SiC", "GaN",
        "advanced packaging", "chiplet", "lithography", "MEMS", "wafer",
        "integrated circuit",
    ],
    "healthcare": [
        "digital health", "medtech", "biotech", "genomics", "mental health",
        "telehealth", "clinical AI", "drug discovery", "diagnostics",
        "wearables", "health data", "precision medicine", "oncology",
        "radiology AI", "EHR", "remote patient monitoring",
        "pathology AI", "surgical robotics", "population health",
        "medical imaging", "health insurance", "rare disease",
        "cell therapy", "gene therapy", "biopharmaceutical",
    ],
    "edtech": [
        "education technology", "edtech", "online learning", "adaptive learning",
        "AI tutoring", "K-12", "higher education", "vocational training",
        "corporate learning", "LMS", "MOOC", "STEM education",
        "language learning", "personalized learning", "assessment platform",
    ],
    "fintech": [
        "fintech", "payments", "digital banking", "neobank", "insurtech",
        "regtech", "blockchain", "cryptocurrency", "DeFi", "robo-advisor",
        "quantitative trading", "algorithmic trading", "lending", "credit tech",
        "embedded finance", "open banking", "wealth management",
    ],
    "greentech": [
        "advanced materials", "sustainable chemistry", "green chemistry",
        "bioplastics", "carbon capture", "CCUS", "circular economy",
        "water treatment", "environmental remediation", "nanomaterials",
        "specialty chemicals", "bio-based materials", "composite materials",
        "cleantech", "green manufacturing",
    ],
    "energy": [
        "solar energy", "photovoltaic", "wind energy", "energy storage",
        "battery", "hydrogen", "fuel cell", "nuclear energy", "SMR",
        "smart grid", "EV charging", "renewable energy", "energy management",
        "biofuel", "clean energy", "energy transition", "grid technology",
    ],
}

# Unified flat list — preserved for the existing `focus` field in extract_fields
FOCUS_KEYWORDS: list[str] = sorted({kw for kws in SECTOR_KEYWORD_MAP.values() for kw in kws})

STAGE_LABELS: dict[str, str | None] = {
    # Tier 1: Angel  (more-specific pre-seed entries must precede bare "seed")
    "angel":      "Angel",
    "pre seed":   "Angel",   # covers "pre-seed" after dash→space normalisation
    "preseed":    "Angel",
    # Tier 2: Pre-A
    "pre a":      "Pre-A",   # covers "pre-a"
    "seed":       "Pre-A",
    "seed+":      "Pre-A",
    "seed plus":  "Pre-A",
    # Tier 3: Series A / B / C (individual labels preserved)
    "series a":   "Series A",
    "series b":   "Series B",
    "series c":   "Series C",
    # Tier 4: Pre-IPO
    "series d":   "Pre-IPO",
    "series e":   "Pre-IPO",
    "series f":   "Pre-IPO",
    "late stage": "Pre-IPO",
    "pre ipo":    "Pre-IPO",
    "growth equity":  "Pre-IPO",
    "growth round":   "Pre-IPO",
    "growth":         "Pre-IPO",
    # Misc (preserved for backward compatibility)
    "early stage":  None,
    "early-stage":  None,
    "venture":      "VC",
}


def normalize_stage(raw: str) -> str | None:
    key = raw.lower().replace("-", " ").strip()
    for pattern, label in STAGE_LABELS.items():
        if pattern in key:
            return label
    return raw.title()


def normalize_region(text: str) -> str | None:
    low = text.lower()
    for keywords, label in REGION_MAP:
        if any(k in low for k in keywords):
            return label
    return None


def extract_firm_name(result) -> str | None:
    url = result.url or ""
    title = (result.title or "").strip()

    cleaned_title = clean_name(title)

    for sep in [" | ", " - ", " – ", " — ", ": "]:
        if sep in title:
            candidate = clean_name(title.split(sep)[0].strip())
            if is_valid_firm_name(candidate):
                return candidate

    text = result.text or ""
    matches = FIRM_RE.findall(text + " " + cleaned_title)
    for m in matches:
        candidate = clean_name(m.strip())
        if is_valid_firm_name(candidate):
            return candidate

    if is_valid_firm_name(cleaned_title):
        return cleaned_title

    return None


FOUNDED_RE = re.compile(
    r"\b(?:founded|established|est\.?|since)\s+(?:in\s+)?(\d{4})\b",
    re.IGNORECASE,
)

PERSON_RE = re.compile(
    r"\b([A-Z][a-z]{1,20}(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]{1,20})\b"
    r"(?:\s*[,–\-]\s*"
    r"(?:Partner|Managing\s+Director|General\s+Partner|GP|Principal|"
    r"Associate|Founder|CEO|CTO|President|MD|VP|Director|Co-Founder|"
    r"Managing\s+Partner|Venture\s+Partner))?",
)

BOILERPLATE_RE = re.compile(
    r"(cookie|privacy policy|terms of|all rights reserved|sign up|log in|"
    r"subscribe|follow us|©|\bnavigation\b|\bmenu\b)",
    re.IGNORECASE,
)

PLATFORM_BOILERPLATE = re.compile(
    r"\b(crunchbase|pitchbook|search crunchbase|start free|log in|sign up|"
    r"investor type|company description|business details|request access|"
    r"solutions\s*products|resources|pricing|advanced search|"
    r"view all|learn more|read more|click here|see all|"
    r"protected content|more contacts|logo|industries|realize the potential|"
    r"is an ai|is a venture|from arkin)\b",
    re.IGNORECASE,
)


def _clean_text_block(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    lines = [ln for ln in text.splitlines() if not PLATFORM_BOILERPLATE.search(ln)]
    return "\n".join(lines)


def extract_description(text: str, firm_name: str) -> str | None:
    text = _clean_text_block(text)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    name_low = firm_name.lower()
    invest_re = re.compile(
        r"\b(invest|partner|back|support|fund|portfolio|focus|speciali[sz]e|"
        r"dedicated|committed|mission|vision|we are|we back|firm seeks)\b",
        re.IGNORECASE,
    )
    for sent in sentences:
        sent = re.sub(r"\s+", " ", sent).strip()
        if re.match(r"^[#*\[\-]", sent) or len(sent) < 50 or len(sent) > 240:
            continue
        if BOILERPLATE_RE.search(sent) or PLATFORM_BOILERPLATE.search(sent):
            continue
        if invest_re.search(sent) or name_low in sent.lower():
            return sent
    for sent in sentences:
        sent = re.sub(r"\s+", " ", sent).strip()
        if re.match(r"^[#*\[\-]", sent):
            continue
        if 60 <= len(sent) <= 200 and not BOILERPLATE_RE.search(sent) and not PLATFORM_BOILERPLATE.search(sent):
            return sent
    return None


def extract_key_people(text: str) -> list[str]:
    text = _clean_text_block(text)
    seen: set[str] = set()
    people: list[str] = []
    titled = re.findall(
        r"\b([A-Z][a-z]{1,20}(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]{1,20})\s*[,–\-\n]\s*"
        r"(?:Partner|Managing\s+Director|General\s+Partner|GP|Principal|"
        r"Co-Founder|Founder|CEO|CTO|CMO|President|MD|VP|Director|"
        r"Managing\s+Partner|Venture\s+Partner|Investment\s+Partner|"
        r"Operating\s+Partner|Senior\s+Partner)",
        text,
    )
    for name in titled:
        name = re.sub(r"\s+", " ", name).strip()
        if PLATFORM_BOILERPLATE.search(name):
            continue
        words = name.split()
        if any(w in {"San", "New", "Los", "United", "North", "South", "East", "West",
                     "Venture", "Capital", "Health", "Stage", "Early", "Late",
                     "Crunchbase", "PitchBook"} for w in words):
            continue
        key = name.lower()
        if key not in seen:
            seen.add(key)
            people.append(name)
    return people[:5]


def extract_portfolio(text: str) -> list[str]:
    text = _clean_text_block(text)
    portfolio: list[str] = []
    seen: set[str] = set()
    company_re = re.compile(
        r"\b((?:[A-Z][a-zA-Z0-9]{1,19}\s+){0,3}[A-Z][a-zA-Z0-9]{1,19}"
        r"\s+(?:Inc\.?|LLC|Ltd\.?|Corp\.?|Technologies|Therapeutics|"
        r"Sciences|Diagnostics|Analytics|Genomics|Pharma|Medical|"
        r"Systems|Biotech|Biosciences|Biotherapeutics|Medicines|"
        r"Health\b|Bio\b|Labs?\b))\b"
    )
    noise_re = re.compile(
        r"\b(logo|about|industries|realize|potential|from|is an|"
        r"more contacts|developer|apis|care|digital)\b",
        re.IGNORECASE,
    )
    for m in company_re.finditer(text):
        name = re.sub(r"\s+", " ", m.group(0)).strip()
        words = name.split()
        if not (2 <= len(words) <= 5):
            continue
        if noise_re.search(name) or PLATFORM_BOILERPLATE.search(name):
            continue
        if not all(w[0].isupper() for w in words):
            continue
        key = name.lower()
        if key not in seen:
            seen.add(key)
            portfolio.append(name)
        if len(portfolio) >= 6:
            break
    return portfolio


def extract_fields(result, sector: str | None = None) -> dict | None:
    title = result.title or ""
    text = (result.text or "") + " " + title
    url = result.url or ""

    firm_name = extract_firm_name(result)
    if not firm_name:
        log.debug("Skipping (no valid firm name): %s", url)
        return None

    # Investment stage
    stage = None
    m = STAGE_RE.search(text)
    if m:
        stage = normalize_stage(m.group(1))

    # Geographic focus
    region = normalize_region(text)

    # Keyword-based focus tags — SECTOR_KEYWORD_MAP is used here only, not for sector inference
    focus = sorted({f for f in FOCUS_KEYWORDS if f.lower() in text.lower()})

    # sector is passed directly from the search-label prefix; no keyword inference needed.

    # Description
    description = extract_description(result.text or "", firm_name)

    # Key people
    key_people = extract_key_people(result.text or "")

    # Portfolio companies
    portfolio = extract_portfolio(result.text or "")

    # Founded year
    founded_year = None
    fy = FOUNDED_RE.search(text)
    if fy:
        yr = int(fy.group(1))
        if 1970 <= yr <= datetime.now().year:
            founded_year = yr

    domain_m = re.match(r"(https?://(?:www\.)?[^/]+)", url)
    website = domain_m.group(1) if domain_m else url

    return {
        "name": firm_name,
        "description": description,
        "stage": stage,
        "region": region,
        "focus": focus,
        "sector": sector,
        "key_people": key_people,
        "portfolio": portfolio,
        "founded_year": founded_year,
        "website": website,
        "source_url": url,
        "scraped_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ── Deduplication ─────────────────────────────────────────────────────────────

def deduplicate(records: list[dict]) -> list[dict]:
    seen_names: set[str] = set()
    seen_urls: set[str] = set()
    unique = []
    for r in records:
        name_key = r["name"].lower().strip()
        url_key = r["source_url"].rstrip("/")
        if name_key in seen_names or (url_key and url_key in seen_urls):
            continue
        seen_names.add(name_key)
        if url_key:
            seen_urls.add(url_key)
        unique.append(r)
    return unique


# ── Helpers ───────────────────────────────────────────────────────────────────

_LEGACY_SECTOR_MAP = {"vc": "healthcare", "health": "healthcare", "drug": "healthcare", "mental": "healthcare"}

def _get_sector_prefix(label: str) -> str:
    """Map a query label to its sector group using the known prefix list."""
    for prefix in SECTOR_PREFIXES:
        if label.startswith(prefix):
            return prefix
    fallback = label.split("_")[0]
    return _LEGACY_SECTOR_MAP.get(fallback, fallback)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    exa = get_exa_client()
    # Store (result, search_label) so extract_fields can derive sector from the label
    all_results: list[tuple] = []

    # Group configs by sector prefix for per-sector progress logging
    sector_groups: dict[str, list[dict]] = defaultdict(list)
    for cfg in SEARCH_CONFIGS:
        prefix = _get_sector_prefix(cfg.get("label", "unknown"))
        sector_groups[prefix].append(cfg)

    log.info("=== Starting scrape: %d sector groups, %d total queries ===",
             len(sector_groups), len(SEARCH_CONFIGS))
    total_sectors = len(sector_groups)
    for idx, (sector_prefix, configs) in enumerate(sector_groups.items(), 1):
        log.info(
            "── Sector [%d/%d]: %-20s  (%d queries)",
            idx, total_sectors, sector_prefix, len(configs),
        )
        for cfg in configs:
            label = cfg.get("label", "unknown")
            label_prefix = _get_sector_prefix(label)
            log.info("  [%s → sector=%s]  %s", label, label_prefix, cfg["query"][:80])
            results = run_search(exa, cfg)
            log.info("  └─ got %d results", len(results))
            all_results.extend((r, label_prefix) for r in results)
        time.sleep(1.0)

    log.info("Total raw results: %d", len(all_results))

    records = []
    rejected = 0
    for r, sec in all_results:
        rec = extract_fields(r, sector=sec)
        if rec:
            records.append(rec)
        else:
            rejected += 1

    log.info("Rejected (bad name): %d  Kept: %d", rejected, len(records))

    records = deduplicate(records)
    log.info("Unique records after dedup: %d", len(records))

    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    import shutil
    shutil.copy2(DATA_PATH, WEB_DATA_PATH)

    log.info("Saved %d records to %s (and web/)", len(records), DATA_PATH)
    return records


if __name__ == "__main__":
    main()
