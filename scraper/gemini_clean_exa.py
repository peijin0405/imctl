import json, os, re, time
from dotenv import load_dotenv
from groq import Groq

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# llama-3.1-8b-instant: 20,000 tokens/min, 14,400 req/day (free tier)
MODEL      = "llama-3.1-8b-instant"
BATCH_SIZE = 10
BATCH_SLEEP = 8   # seconds between batches — keeps token/min < 20,000

FIELDS = [
    "name", "investor_type", "sector", "sectors",
    "stage", "focus", "hq_state", "hq_city",
    "website", "description", "key_people", "portfolio",
]

SYSTEM_PROMPT = """You are a VC/PE database cleaning assistant. Process a batch of investor records and return a JSON array.

VALIDATION rules (mark valid=false if ANY applies):
- Not a real investment firm (VC, PE, hedge fund, family office, corporate VC)
- Is a person's name (e.g. "Katherine Wang", "Andy Strunk")
- Is a portfolio company / startup (e.g. "Vibe Bio", "Radiant Nuclear", "Multimodal")
- Is a generic term, description, or website name
- Not a US-based institution

CLEANING rules (only for valid records):
- name: keep original casing, trim spaces, remove non-firm suffixes like "- Quantum Computing"
- investor_type: one of: VC / PE / Hedge Fund / Family Office / Corporate VC / Investment Adviser
- sector: one of: ai_apps / ai_hardware / semiconductor / healthcare / edtech / fintech / greentech / energy / general
- stage: list, elements only: Pre-Seed / Seed / Series A / Series B / Series C+ / Growth / Buyout / Multi-Stage
- focus: keyword list, each ≤30 chars, remove filler words
- hq_state: 2-letter US state code, null if unknown
- hq_city: title case city name, null if unknown
- website: full URL starting with https://, null if unknown
- description: concise English, ≤120 words, null if unknown
- key_people: [{"name": str, "title": str}], formal titles only
- portfolio: list of real investee company names only

Return ONLY a JSON array, no extra text:
[
  {"i": 0, "valid": true, "cleaned": {"name": ..., "investor_type": ..., "sector": ..., "sectors": [...], "stage": [...], "focus": [...], "hq_state": ..., "hq_city": ..., "website": ..., "description": ..., "key_people": [...], "portfolio": [...]}},
  {"i": 1, "valid": false, "reason": "is a startup, not an investor"},
  ...
]"""


def clean_batch(records: list[dict]) -> list[dict | None]:
    """
    Send up to BATCH_SIZE records to Groq in one call.
    Returns list of same length: cleaned dict or None (if invalid/deleted).
    """
    payload = [
        {"i": idx, **{f: r.get(f) for f in FIELDS}}
        for idx, r in enumerate(records)
    ]
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Clean these {len(records)} records:\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )

    backoff = 10
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=2500,
            )
            text = response.choices[0].message.content.strip()
            text = text.replace("```json", "").replace("```", "").strip()
            items = json.loads(text)

            # Build result indexed by "i"
            results = [None] * len(records)  # default: keep original if missing
            for item in items:
                idx = int(item.get("i", 0))
                if not (0 <= idx < len(records)):
                    continue
                if not item.get("valid", True):
                    results[idx] = False  # sentinel: delete
                else:
                    cleaned = item.get("cleaned", {})
                    merged = {**records[idx]}
                    for k, v in cleaned.items():
                        if v is not None and v != [] and v != "":
                            merged[k] = v
                    results[idx] = merged
            return results

        except json.JSONDecodeError as e:
            print(f"    JSON解析失败 (attempt {attempt+1}/3): {e}")
            time.sleep(3)
        except Exception as e:
            err = str(e)
            if "429" in err or "rate" in err.lower():
                m = re.search(r"Please try again in ([\d.]+)s", err)
                wait = float(m.group(1)) + 3 if m else backoff
                print(f"    429限流，等待 {wait:.0f}s...")
                time.sleep(wait)
                backoff = min(backoff * 2, 120)
            else:
                print(f"    API错误: {err[:120]}")
                time.sleep(backoff)

    # All attempts failed — keep originals unchanged
    print(f"    ⚠️ 批次全部失败，保留原始记录")
    return list(records)


def main():
    with open("web/investors.json") as f:
        all_data = json.load(f)

    EXA_SOURCES = {
        "exa_directory", "exa_vc_directories",
        "authority_list", "pdf_report", "exa_direct"
    }
    exa_records = [d for d in all_data if d.get("data_source", "") in EXA_SOURCES]
    sec_records  = [d for d in all_data if d.get("data_source", "") not in EXA_SOURCES]

    print(f"总记录: {len(all_data)}")
    print(f"SEC来源（不处理）: {len(sec_records)}")
    print(f"Exa来源（待清洗）: {len(exa_records)}")

    total_batches = (len(exa_records) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"批次数: {total_batches}（每批 {BATCH_SIZE} 条，间隔 {BATCH_SLEEP}s）")
    print(f"预计耗时: ~{total_batches * BATCH_SLEEP // 60} 分钟")

    backup_path = "data/investors_before_gemini_clean.json"
    if not os.path.exists(backup_path):
        with open(backup_path, "w") as f:
            json.dump(all_data, f, ensure_ascii=False, indent=2)
        print(f"✅ 已备份到 {backup_path}")
    else:
        print(f"✅ 备份已存在，跳过")

    progress_file = "data/gemini_clean_progress.json"
    completed_ids: dict = {}
    if os.path.exists(progress_file):
        with open(progress_file) as f:
            completed_ids = json.load(f)
        print(f"断点续爬：已处理 {len(completed_ids)} 条")

    valid_count   = 0
    invalid_count = 0
    rejected_names: list[str] = []
    cleaned_records: list[dict] = []

    # Rebuild already-processed records into cleaned_records
    for record in exa_records:
        rec_id = record.get("id") or record.get("name", "")
        if rec_id in completed_ids:
            result = completed_ids[rec_id]
            if result:
                cleaned_records.append(result)
                valid_count += 1
            else:
                invalid_count += 1
                rejected_names.append(record.get("name", ""))

    # Find unprocessed records
    pending = [
        r for r in exa_records
        if (r.get("id") or r.get("name", "")) not in completed_ids
    ]
    print(f"待处理: {len(pending)} 条")

    batch_num = 0
    for batch_start in range(0, len(pending), BATCH_SIZE):
        batch = pending[batch_start: batch_start + BATCH_SIZE]
        batch_num += 1
        names_preview = ", ".join(r.get("name", "?")[:20] for r in batch[:3])
        print(f"[批次 {batch_num}/{total_batches - len(completed_ids)//BATCH_SIZE}] "
              f"第 {batch_start+1}–{batch_start+len(batch)} 条: {names_preview}...")

        results = clean_batch(batch)

        for record, result in zip(batch, results):
            rec_id = record.get("id") or record.get("name", "")
            if result is False:
                # Explicitly marked invalid
                completed_ids[rec_id] = None
                invalid_count += 1
                rejected_names.append(record.get("name", ""))
                print(f"  ❌ {record.get('name')}")
            elif result is None:
                # Missing from response — keep original
                completed_ids[rec_id] = record
                cleaned_records.append(record)
                valid_count += 1
            else:
                completed_ids[rec_id] = result
                cleaned_records.append(result)
                valid_count += 1

        # Save progress after every batch
        with open(progress_file, "w") as f:
            json.dump(completed_ids, f, ensure_ascii=False)

        processed_total = len(completed_ids)
        print(f"  💾 已处理 {processed_total}/{len(exa_records)}，"
              f"有效={valid_count} 删除={invalid_count}")

        if batch_start + BATCH_SIZE < len(pending):
            time.sleep(BATCH_SLEEP)

    # Write final output
    final = sec_records + cleaned_records

    with open("web/investors.json", "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)

    with open("data/investors_final.json", "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)

    print(f"""
{'='*55}
✅ Groq 批量清洗完成
{'='*55}
Exa 原始记录:     {len(exa_records):,}
  保留（有效）:   {valid_count:,}
  删除（无效）:   {invalid_count:,}
  删除率:         {100*invalid_count//len(exa_records) if exa_records else 0}%

SEC 记录（未动）: {len(sec_records):,}
最终总记录:       {len(final):,}

被删除的记录（前30条）:
{chr(10).join(f'  - {n}' for n in rejected_names[:30])}

输出文件:
  web/investors.json
  data/investors_final.json
{'='*55}
""")


if __name__ == "__main__":
    main()
