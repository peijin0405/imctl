"""
四层 Exa 数据深度清洗流水线
Usage:
  python scraper/exa_deep_clean.py --test          # 只跑前20条验证逻辑
  python scraper/exa_deep_clean.py --skip-l4       # 跳过第四层
  python scraper/exa_deep_clean.py                 # 全量运行
  python scraper/exa_deep_clean.py --resume        # 断点续爬
  python scraper/exa_deep_clean.py --layer 3       # 只运行指定层
"""

import re
import json
import os
import time
import hashlib
import argparse
from datetime import datetime
from urllib.parse import urlparse
from collections import Counter

from dotenv import load_dotenv
from groq import Groq
from exa_py import Exa

load_dotenv()

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
exa_client  = Exa(os.getenv("EXA_API_KEY"))

# ── 数据源标识 ─────────────────────────────────────────────────────────────
EXA_SOURCES = {
    "exa_directory", "exa_vc_directories",
    "authority_list", "pdf_report",
}

# ── 机构特征词 ─────────────────────────────────────────────────────────────
FIRM_SUFFIXES = [
    "ventures", "venture", "capital", "partners", "fund", "funds",
    "investments", "equity", "management", "advisors", "advisers",
    "holdings", "group", "associates", "asset", "growth", "vc", "pe",
    "investment", "health", "bio", "tech", "energy", "innovation",
    "innovations", "labs", "solutions", "technologies", "financial",
    "securities", "trust", "wealth", "global", "international",
    "strategic", "corp", "inc",
]

# ── 地理名称（直接删除）────────────────────────────────────────────────────
GEO_NAMES = {
    "saudi arabia", "south korea", "dominican republic", "dr congo",
    "drc congo", "central african republic", "united arab emirates",
    "india", "switzerland", "uk", "poland", "taiwan", "china",
    "singapore", "japan", "germany", "france", "israel",
}

# ── 噪声短语（名称含以下词即删除）────────────────────────────────────────
NOISE_PHRASES = [
    " shared", "breaking news", "privacy policy", "contact us",
    "archives", "recent news", "recent investments", "rising stars",
    "companies", "portfolio at", "investment in ", "spotlight:",
    "active venture capital firms", "different kind of",
    "table of contents", "venture dealsclosed", "fund lists",
    "running list", "who are the", "top 20", "top 10",
    "a running list", "why is ", "how to", "what is",
    "partners to crypto builders since",
    "we are an early-stage",
    "create the future",
]

# ── 行业关键词 ─────────────────────────────────────────────────────────────
SECTOR_KEYWORDS = {
    "ai_apps":      ["artificial intelligence", "machine learning", "llm",
                     "generative ai", "enterprise ai", "foundation model"],
    "ai_hardware":  ["robotics", "autonomous vehicle", "self-driving",
                     "industrial automation", "embodied ai", "drone"],
    "semiconductor":["semiconductor", "chip", "fabless", "fpga", "gpu",
                     "integrated circuit", "chiplet", "photonics"],
    "healthcare":   ["healthcare", "biotech", "pharmaceutical", "life sciences",
                     "medical", "therapeutics", "genomics", "digital health"],
    "edtech":       ["education", "edtech", "e-learning", "online learning",
                     "workforce training"],
    "fintech":      ["fintech", "financial technology", "payments", "blockchain",
                     "cryptocurrency", "quantitative", "insurtech"],
    "greentech":    ["cleantech", "sustainability", "carbon", "recycling",
                     "circular economy", "esg", "climate"],
    "energy":       ["clean energy", "renewable energy", "solar", "battery",
                     "hydrogen", "electric vehicle", "decarbonization"],
}

# ── 投资阶段关键词 ────────────────────────────────────────────────────────
STAGE_KW = {
    "Pre-Seed":   ["pre-seed", "pre seed"],
    "Seed":       ["seed stage", "seed fund", "seed capital", "early stage"],
    "Series A":   ["series a", "series-a"],
    "Series B":   ["series b"],
    "Series C+":  ["series c", "late stage", "pre-ipo"],
    "Growth":     ["growth equity", "growth capital"],
    "Buyout":     ["buyout", "lbo"],
    "Multi-Stage":["multi-stage", "all stages"],
}

# ── 城市→州映射 ───────────────────────────────────────────────────────────
CITY_STATE = {
    "san francisco": "CA", "silicon valley": "CA", "palo alto": "CA",
    "menlo park": "CA",    "los angeles": "CA",    "san jose": "CA",
    "san diego": "CA",     "new york": "NY",        "nyc": "NY",
    "manhattan": "NY",     "brooklyn": "NY",        "boston": "MA",
    "cambridge": "MA",     "chicago": "IL",         "austin": "TX",
    "dallas": "TX",        "houston": "TX",         "seattle": "WA",
    "denver": "CO",        "miami": "FL",            "atlanta": "GA",
    "washington dc": "DC", "washington d.c.": "DC",
}

# ── 聚合类网站（Exa 搜索时排除）─────────────────────────────────────────
AGGREGATORS = [
    "crunchbase.com", "pitchbook.com", "linkedin.com", "wikipedia.org",
    "bloomberg.com",  "techcrunch.com", "forbes.com",  "cbinsights.com",
    "wsj.com",        "reuters.com",    "ft.com",
]


# ══════════════════════════════════════════════════════════════════════════
# 第一层：本地规则硬过滤
# ══════════════════════════════════════════════════════════════════════════

def layer1_hard_filter(records: list[dict]) -> tuple[list, list]:
    kept     = []
    rejected = []

    for rec in records:
        name       = (rec.get("name") or "").strip()
        name_lower = name.lower()
        reason     = None

        if not name or len(name) < 4:
            reason = "名称过短或为空"
        elif name_lower in GEO_NAMES:
            reason = f"地理名称: {name}"
        elif any(noise in name_lower for noise in NOISE_PHRASES):
            reason = "含噪声短语"
        elif re.match(r'^\d+\s+(investments|shared)', name_lower):
            reason = "投资数量统计，非机构名"
        elif len(name.split()) > 8:
            reason = "名称过长，疑似句子"
        elif re.search(r'(https?://|@|\.(com|org|io)\b)', name_lower):
            reason = "含URL或邮箱"
        elif not any(s in name_lower for s in FIRM_SUFFIXES):
            if len(name.split()) > 4:
                reason = "无机构特征词且词数过多"
            elif _is_pure_person_name(name):
                reason = "疑似人名（无机构特征词）"

        if reason:
            rejected.append({**rec, "_reject_reason": reason})
        else:
            kept.append(rec)

    return kept, rejected


def _is_pure_person_name(name: str) -> bool:
    words = name.strip().split()
    if len(words) not in (2, 3):
        return False
    if not all(w[0].isupper() for w in words if w):
        return False
    if any(s in name.lower() for s in FIRM_SUFFIXES):
        return False
    if any(c.isdigit() for c in name):
        return False
    return True


# ══════════════════════════════════════════════════════════════════════════
# 第二层：名称标准化
# ══════════════════════════════════════════════════════════════════════════

def layer2_normalize_name(records: list[dict]) -> list[dict]:
    cleaned = []
    for rec in records:
        raw  = (rec.get("name") or "").strip()
        new  = _clean_name(raw)
        cleaned.append({**rec, "name": new, "_original_name": raw})
    return cleaned


def _clean_name(name: str) -> str:
    # 1. 去掉 "N shared" 后缀
    name = re.sub(r'\s+\d+\s+shared\s*$', '', name, flags=re.I).strip()

    # 2. 去掉职位后缀（· 或 • 后）
    name = re.sub(
        r'\s*[·•]\s*(General Partner|Managing Partner|Partner|'
        r'Managing Director|Director|CEO|CIO|Founder|Co-Founder|'
        r'Principal|Analyst|Associate|Vice President|Venture Partner)'
        r'.*$', '', name, flags=re.I
    ).strip()

    # 3. 去掉 " - 描述词" 后缀
    name = re.sub(r'\s+[-–]\s+[A-Z][a-zA-Z\s]{3,}$', '', name).strip()

    # 4. 去掉末尾的网站/页面词
    name = re.sub(
        r'\s+(Website|Homepage|About Us|About|Blog|Portfolio|'
        r'Team|Contact|News|Press|Careers)\s*$',
        '', name, flags=re.I
    ).strip()

    # 5. 处理"人名+机构名"拼接
    name = _extract_firm_from_combined(name)

    # 6. 去掉数字编号前缀
    name = re.sub(r'^\d+\.\s+', '', name).strip()

    # 7. 合并多余空格
    name = re.sub(r'\s+', ' ', name).strip()

    return name


def _extract_firm_from_combined(name: str) -> str:
    words = name.split()
    if len(words) <= 3:
        return name
    for i in range(1, len(words)):
        suffix_window = " ".join(words[i:]).lower()
        if any(s in suffix_window for s in FIRM_SUFFIXES):
            prefix       = " ".join(words[:i])
            prefix_words = prefix.split()
            is_person = (
                all(w[0].isupper() for w in prefix_words if w) and
                not any(s in prefix.lower() for s in FIRM_SUFFIXES) and
                1 <= len(prefix_words) <= 3
            )
            if is_person:
                firm_part = " ".join(words[i:])
                if len(firm_part.split()) >= 2 or \
                   any(s in firm_part.lower() for s in FIRM_SUFFIXES):
                    return firm_part
    return name


# ══════════════════════════════════════════════════════════════════════════
# 第三层：Groq AI 判断 + 字段重建
# ══════════════════════════════════════════════════════════════════════════

GROQ_MODEL = "llama-3.3-70b-versatile"

GROQ_JUDGE_PROMPT = """You are an investment firm database auditor. Determine if the following name is a real US-based investment institution.

Name: {name}

Investment institutions include: VC, PE, hedge funds, family offices, corporate VC (CVC).
NOT investment institutions: individual person names, portfolio companies/startups, descriptive phrases, website page names, non-investment organizations.

Return ONLY valid JSON, no extra text:
{{
  "is_valid": true or false,
  "reason": "one sentence reason",
  "standard_name": "standardized institution name (remove extra descriptors)",
  "known_info": {{
    "investor_type": "VC/PE/Hedge Fund/Family Office/Corporate VC or null",
    "hq_state": "two-letter US state code or null",
    "hq_city": "city name or null",
    "founded_year": year as integer or null,
    "description": "description in 50 words or less, or null",
    "is_well_known": true or false
  }}
}}"""


def layer3_groq_judge(records: list[dict], progress: dict) -> tuple[list, list]:
    valid   = []
    invalid = []

    for i, rec in enumerate(records):
        name   = rec.get("name", "")
        rec_id = _rec_id(rec)

        if rec_id in progress.get("layer3", {}):
            cached = progress["layer3"][rec_id]
            if cached:
                valid.append({**rec, **cached})
            else:
                invalid.append({**rec, "_reject_reason": "Groq判定为无效（缓存）"})
            continue

        print(f"  [L3 {i+1}/{len(records)}] {name}")
        result = _call_groq_judge(name)

        if result and result.get("is_valid"):
            known   = result.get("known_info", {})
            updated = {**rec}
            if result.get("standard_name"):
                updated["name"] = result["standard_name"]
            for field, key in [
                ("investor_type", "investor_type"),
                ("hq_state",      "hq_state"),
                ("hq_city",       "hq_city"),
                ("founded_year",  "founded_year"),
                ("description",   "description"),
            ]:
                if known.get(key):
                    updated[field] = known[key]
            updated["_groq_known"] = known.get("is_well_known", False)

            progress.setdefault("layer3", {})[rec_id] = updated
            valid.append(updated)
        else:
            reason = (result.get("reason") if result else None) or "API调用失败"
            progress.setdefault("layer3", {})[rec_id] = None
            invalid.append({**rec, "_reject_reason": reason})
            print(f"    ❌ {name} ({reason})")

        time.sleep(0.3)

    return valid, invalid


def _call_groq_judge(name: str) -> dict | None:
    prompt = GROQ_JUDGE_PROMPT.format(name=name)
    for attempt in range(3):
        try:
            resp = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=350,
            )
            text = resp.choices[0].message.content.strip()
            text = re.sub(r'```json\s*', '', text)
            text = re.sub(r'```\s*', '', text).strip()
            return _safe_parse_json(text)
        except Exception as e:
            err = str(e)
            if "429" in err or "rate" in err.lower():
                m    = re.search(r'Please try again in ([\d.]+)s', err)
                wait = float(m.group(1)) + 2 if m else 10 * (2 ** attempt)
                print(f"    429限流，等待 {wait:.0f}s…")
                time.sleep(wait)
            else:
                print(f"    Groq error (attempt {attempt+1}): {err[:100]}")
                time.sleep(2 ** attempt)
    return None


# ══════════════════════════════════════════════════════════════════════════
# 第四层：Exa 二次查询
# ══════════════════════════════════════════════════════════════════════════

def layer4_exa_reenrich(records: list[dict], progress: dict) -> list[dict]:
    enriched = []

    for i, rec in enumerate(records):
        name   = rec.get("name", "")
        sector = rec.get("sector", "general")
        rec_id = _rec_id(rec)

        if rec_id in progress.get("layer4", {}):
            enriched.append(progress["layer4"][rec_id])
            continue

        print(f"  [L4 {i+1}/{len(records)}] Exa: {name}")
        exa_data = _exa_search_firm(name, sector)
        merged   = _merge_exa_into_record(rec, exa_data) if exa_data else {**rec}
        merged["_exa_reenriched"] = True
        merged["scraped_at"]      = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        progress.setdefault("layer4", {})[rec_id] = merged
        enriched.append(merged)
        time.sleep(0.5)

    return enriched


def _exa_search_firm(name: str, sector: str) -> dict | None:
    query = (f"{name} venture capital investment firm "
             f"United States official website portfolio")
    try:
        result = exa_client.search_and_contents(
            query,
            num_results=1,
            type="neural",
            use_autoprompt=False,
            text=True,
            highlights={"num_sentences": 10, "highlights_per_url": 5},
            exclude_domains=AGGREGATORS,
        )
    except Exception as e:
        print(f"    Exa error: {e}")
        return None

    if not result.results:
        return None

    r    = result.results[0]
    url  = r.url or ""
    text = (r.text or "") + " " + " ".join(r.highlights or [])

    US_GEO = [
        "united states", " usa", " u.s.", "new york", "san francisco",
        "silicon valley", "boston", "los angeles", "chicago", "austin",
        "seattle", "denver", "miami", "atlanta", "washington",
        "menlo park", "palo alto", "cambridge",
    ]
    FOREIGN = [
        "london", "berlin", "paris", "beijing", "shanghai",
        "singapore", "tokyo", ".co.uk", ".de", ".fr", ".cn",
    ]

    if not any(g in text.lower() for g in US_GEO):
        if any(f in url.lower() for f in FOREIGN):
            print(f"    ⚠️  非美国机构，跳过")
            return None

    return {"url": url, "text": text, "highlights": r.highlights or []}


def _merge_exa_into_record(rec: dict, exa_data: dict) -> dict:
    text   = exa_data["text"]
    url    = exa_data["url"]
    merged = {**rec}

    # 官网
    if url and not any(a in url for a in AGGREGATORS):
        parsed          = urlparse(url)
        merged["website"] = f"{parsed.scheme}://{parsed.netloc}"

    # 行业
    primary, sectors, focus = _infer_sectors(text)
    if primary != "general":
        merged["sector"]  = primary
        merged["sectors"] = sectors
        merged["focus"]   = focus[:10]

    # 地理（Groq 已有则保留）
    if not merged.get("hq_state"):
        state, city = _infer_location(text)
        if state: merged["hq_state"] = state
        if city:  merged["hq_city"]  = city

    # 投资阶段
    if not merged.get("stage"):
        stages = _infer_stages(text)
        if stages: merged["stage"] = stages

    # 描述
    if not merged.get("description"):
        desc = text[:250].strip()
        if len(desc) > 50:
            merged["description"] = desc

    # Portfolio / Key People / Contacts
    portfolio = _extract_portfolio(text)
    if portfolio: merged["portfolio"] = portfolio

    people = _extract_people(text)
    if people: merged["key_people"] = people

    contacts = _extract_contacts(text)
    if contacts: merged["contacts"] = contacts

    return merged


# ── 字段提取辅助 ───────────────────────────────────────────────────────────

def _infer_sectors(text: str) -> tuple:
    t = text.lower()
    scores  = {}
    matched = {}
    for sec, kws in SECTOR_KEYWORDS.items():
        hits = [kw for kw in kws if kw in t]
        if hits:
            scores[sec]  = len(hits)
            matched[sec] = hits
    if not scores:
        return "general", [], []
    primary  = max(scores, key=scores.get)
    all_secs = sorted(scores, key=scores.get, reverse=True)
    return primary, all_secs, matched.get(primary, [])[:10]


def _infer_location(text: str) -> tuple:
    t = text.lower()
    for city, state in CITY_STATE.items():
        if city in t:
            return state, city.title()
    return None, None


def _infer_stages(text: str) -> list:
    t = text.lower()
    return [s for s, kws in STAGE_KW.items() if any(kw in t for kw in kws)]


def _extract_portfolio(text: str) -> list:
    portfolio = []
    patterns = [
        r'portfolio (?:companies|investments|include)[:\s]+([A-Z][^.]{10,100})',
        r'invested in ([A-Z][a-zA-Z\s,]{10,80})',
        r'backed (?:companies)?[:\s]+([A-Z][^.]{10,80})',
    ]
    for pat in patterns:
        for m in re.findall(pat, text):
            names = [n.strip() for n in m.split(",") if 2 <= len(n.strip()) <= 40]
            portfolio.extend(names[:5])
    return list(set(portfolio))[:15]


def _extract_people(text: str) -> list:
    people = []
    pat = (r'([\w\s]{3,30}),?\s+'
           r'(General Partner|Managing Partner|Partner|Founder|'
           r'Co-Founder|Managing Director|Principal|CEO|CIO|CFO)')
    for name, title in re.findall(pat, text)[:8]:
        name = name.strip()
        if 2 <= len(name.split()) <= 4:
            people.append({"name": name, "title": title})
    return people


def _extract_contacts(text: str) -> list:
    contacts  = []
    emails    = re.findall(r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}', text)
    phones    = re.findall(r'\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', text)
    linkedins = re.findall(r'linkedin\.com/in/[\w-]+', text)

    for person in _extract_people(text):
        contact = {
            "name":       person["name"],
            "title":      person["title"],
            "email":      None,
            "phone":      None,
            "linkedin":   None,
            "confidence": "medium",
        }
        pos    = text.find(person["name"])
        nearby = text[max(0, pos-200):pos+200] if pos != -1 else ""
        ne = re.findall(r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}', nearby)
        np_ = re.findall(r'\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', nearby)
        nl  = re.findall(r'linkedin\.com/in/[\w-]+', nearby)
        if ne:  contact["email"]    = ne[0];  contact["confidence"] = "high"
        if np_: contact["phone"]    = np_[0]; contact["confidence"] = "high"
        if nl:  contact["linkedin"] = "https://" + nl[0]; contact["confidence"] = "high"
        contacts.append(contact)

    if not contacts and emails:
        contacts.append({
            "name":       None,
            "title":      None,
            "email":      emails[0],
            "phone":      phones[0] if phones else None,
            "linkedin":   ("https://" + linkedins[0]) if linkedins else None,
            "confidence": "low",
        })
    return contacts[:5]


# ══════════════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════════════

def _rec_id(rec: dict) -> str:
    return rec.get("id") or hashlib.md5(
        (rec.get("name") or "").lower().encode()
    ).hexdigest()[:12]


def _safe_parse_json(text: str) -> dict | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        start = text.index('{')
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i+1])
    except (ValueError, json.JSONDecodeError):
        pass
    return None


def calc_completeness(d: dict) -> int:
    weights = {
        "name":10, "website":10, "hq_state":8, "investor_type":8,
        "sector":8, "aum_usd":10, "description":8, "key_people":8,
        "portfolio":8, "contacts":10, "founded_year":5, "focus":7,
    }
    score = 0
    for field, w in weights.items():
        val = d.get(field)
        if val and val not in ([], {}, "", None):
            score += w
    return score


def save_progress(progress: dict, path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False)


def load_progress(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


# ══════════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="四层 Exa 数据深度清洗")
    parser.add_argument("--layer",   type=int, choices=[1, 2, 3, 4],
                        help="只运行指定层（默认全部）")
    parser.add_argument("--resume",  action="store_true", help="断点续爬")
    parser.add_argument("--test",    action="store_true", help="测试模式：只处理前20条")
    parser.add_argument("--skip-l4", action="store_true", help="跳过第四层（Exa积分不足时）")
    args = parser.parse_args()

    # ── 加载数据 ──────────────────────────────────────────────────────────
    with open("web/investors.json", encoding="utf-8") as f:
        all_data = json.load(f)

    exa_records = [d for d in all_data if d.get("data_source", "") in EXA_SOURCES]
    sec_records  = [d for d in all_data if d.get("data_source", "") not in EXA_SOURCES]

    print(f"{'='*55}")
    print(f"  四层清洗流水线启动")
    print(f"{'='*55}")
    print(f"  SEC 记录（不处理）: {len(sec_records):,}")
    print(f"  Exa 记录（待清洗）: {len(exa_records):,}")

    # ── 备份（在 test slice 之前，保存完整数据）────────────────────────────
    backup_path = "data/exa_before_deep_clean.json"
    if not os.path.exists(backup_path):
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(exa_records, f, ensure_ascii=False, indent=2)
        print(f"  ✅ 已备份到 {backup_path}")
    else:
        print(f"  ✅ 备份已存在，跳过")

    if args.test:
        exa_records = exa_records[:20]
        print(f"  [测试模式] 只处理前 20 条（不写入 investors.json）")

    # ── 断点进度 ──────────────────────────────────────────────────────────
    progress_path = "data/deep_clean_progress.json"
    progress      = load_progress(progress_path) if args.resume else {}

    # ══════════════════════════════════════════════════════════════════════
    # 第一层
    # ══════════════════════════════════════════════════════════════════════
    print(f"\n{'─'*55}")
    print(f"第一层：本地规则硬过滤")
    kept_l1, rejected_l1 = layer1_hard_filter(exa_records)
    print(f"  通过: {len(kept_l1)} | 删除: {len(rejected_l1)}")
    for r in rejected_l1[:15]:
        print(f"  ❌ {r.get('name', '?'):40s} → {r['_reject_reason']}")

    if args.layer == 1:
        print("  [--layer 1] 停止。")
        return

    # ══════════════════════════════════════════════════════════════════════
    # 第二层
    # ══════════════════════════════════════════════════════════════════════
    print(f"\n{'─'*55}")
    print(f"第二层：名称标准化")
    kept_l2 = layer2_normalize_name(kept_l1)
    changed = [
        (r["_original_name"], r["name"])
        for r in kept_l2
        if r.get("_original_name") != r.get("name")
    ]
    print(f"  名称修正: {len(changed)} 条")
    for orig, new in changed[:15]:
        print(f"  ✏️  '{orig[:45]}' → '{new}'")

    if args.layer == 2:
        print("  [--layer 2] 停止。")
        return

    # ══════════════════════════════════════════════════════════════════════
    # 第三层
    # ══════════════════════════════════════════════════════════════════════
    print(f"\n{'─'*55}")
    print(f"第三层：Groq AI 判断 + 字段重建")
    kept_l3, rejected_l3 = layer3_groq_judge(kept_l2, progress)
    save_progress(progress, progress_path)
    print(f"  通过: {len(kept_l3)} | 删除: {len(rejected_l3)}")

    if args.layer == 3:
        print("  [--layer 3] 停止。")
        return

    # ══════════════════════════════════════════════════════════════════════
    # 第四层
    # ══════════════════════════════════════════════════════════════════════
    if args.skip_l4:
        print(f"\n{'─'*55}")
        print(f"第四层：已跳过（--skip-l4）")
        final_exa = kept_l3
    else:
        print(f"\n{'─'*55}")
        print(f"第四层：Exa 二次查询（用干净名称重新获取信息）")
        final_exa = layer4_exa_reenrich(kept_l3, progress)
        save_progress(progress, progress_path)
        print(f"  完成: {len(final_exa)} 条")

    if args.layer == 4:
        print("  [--layer 4] 停止（未写出）。")
        return

    # 测试模式：只打印结果，不写文件
    if args.test:
        print(f"\n[测试模式] 处理完成，结果未写入磁盘。")
        print(f"  通过记录: {len(final_exa)}")
        for r in final_exa:
            print(f"    ✅ {r.get('name')}")
        return

    # ══════════════════════════════════════════════════════════════════════
    # 重算完整度评分 + 清理内部字段
    # ══════════════════════════════════════════════════════════════════════
    for rec in final_exa:
        rec["completeness_score"] = calc_completeness(rec)
        for k in ("_original_name", "_reject_reason", "_groq_known", "_exa_reenriched"):
            rec.pop(k, None)

    # ── 合并写入 ──────────────────────────────────────────────────────────
    final_all = sec_records + final_exa

    with open("web/investors.json", "w", encoding="utf-8") as f:
        json.dump(final_all, f, ensure_ascii=False, indent=2)
    with open("data/investors_final.json", "w", encoding="utf-8") as f:
        json.dump(final_all, f, ensure_ascii=False, indent=2)

    all_rejected = rejected_l1 + rejected_l3
    with open("data/deep_clean_rejected.json", "w", encoding="utf-8") as f:
        json.dump(all_rejected, f, ensure_ascii=False, indent=2)

    # ── 最终报告 ──────────────────────────────────────────────────────────
    sectors = Counter(d.get("sector") for d in final_exa)
    scores  = [d.get("completeness_score", 0) for d in final_exa]
    avg     = sum(scores) / len(scores) if scores else 0

    sector_lines = "".join(
        f"  {(s or '?'):15s} {c}\n" for s, c in sectors.most_common()
    )

    print(f"""
{'='*55}
✅ 四层清洗完成
{'='*55}
Exa 原始:          {len(exa_records):,}
  第一层删除:       {len(rejected_l1):,}（本地规则）
  第二层名称修正:   {len(changed):,}
  第三层删除:       {len(rejected_l3):,}（AI判断）
  第四层重新富化:   {len(final_exa):,}

最终 Exa 保留:     {len(final_exa):,}
最终总记录:        {len(final_all):,}

行业分布:
{sector_lines}
完整度评分:
  平均分:   {avg:.1f}
  80+ 分:   {sum(1 for s in scores if s >= 80)}
  50-79 分: {sum(1 for s in scores if 50 <= s < 80)}
  50 以下:  {sum(1 for s in scores if s < 50)}

输出文件:
  web/investors.json              ✅
  data/investors_final.json       ✅
  data/deep_clean_rejected.json   ✅（{len(all_rejected)} 条被删记录）
{'='*55}
""")


if __name__ == "__main__":
    main()
