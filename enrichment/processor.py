"""Core enrichment processor — API call + agentic loop + JSON parsing."""

import asyncio
import json
import re
import time
from typing import Optional

import aiohttp
import anthropic

from .prompts import SYSTEM_PROMPT, build_user_prompt
from .validator import validate

MODEL      = "claude-haiku-4-5-20251001"
MAX_TOKENS = 4000
MAX_RETRIES = 5
RATE_LIMIT_WAIT = 60.0  # seconds per retry increment on RateLimitError

# web_search — server-side tool (Anthropic executes it)
WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
}

# web_fetch — client-side tool (we execute with aiohttp)
WEB_FETCH_TOOL = {
    "name": "web_fetch",
    "description": "Fetch the full text content of a URL. Use this to read a firm's website pages.",
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to fetch"},
        },
        "required": ["url"],
    },
}


async def _do_fetch(url: str, session: aiohttp.ClientSession) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20), headers=headers) as resp:
            if resp.status != 200:
                return f"HTTP {resp.status} — {url}"
            html = await resp.text(errors="ignore")
            # Strip script/style blocks
            html = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", "", html, flags=re.S | re.I)
            # Strip remaining tags
            text = re.sub(r"<[^>]+>", " ", html)
            # Collapse whitespace
            text = re.sub(r"\s+", " ", text).strip()
            return text[:10000]
    except Exception as e:
        return f"Fetch error: {e}"


async def enrich_one(
    record: dict,
    client: anthropic.AsyncAnthropic,
    http_session: aiohttp.ClientSession,
) -> dict:
    """
    Returns result dict:
      status: "enriched" | "rejected" | "failed"
      original, enriched, rejection_reason, error
      tokens_input, tokens_output, elapsed_s
    """
    t0 = time.monotonic()
    tokens_in = tokens_out = 0

    messages = [{"role": "user", "content": build_user_prompt(record)}]
    system = [{
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }]

    last_error = "unknown"
    for attempt in range(MAX_RETRIES):
        try:
            raw_json, ti, to = await _agentic_loop(client, http_session, system, messages)
            tokens_in  += ti
            tokens_out += to

            is_valid, msg, cleaned = validate(raw_json)
            elapsed = time.monotonic() - t0

            if not is_valid and raw_json.get("verification", {}).get("is_real_investor") is False:
                return {
                    "status": "rejected",
                    "original": record,
                    "rejection_reason": msg,
                    "tokens_input": tokens_in,
                    "tokens_output": tokens_out,
                    "elapsed_s": round(elapsed, 2),
                }

            if not is_valid:
                return {
                    "status": "failed",
                    "original": record,
                    "error": f"validation: {msg}",
                    "raw": raw_json,
                    "tokens_input": tokens_in,
                    "tokens_output": tokens_out,
                    "elapsed_s": round(elapsed, 2),
                }

            return {
                "status": "enriched",
                "original": record,
                "enriched": cleaned,
                "tokens_input": tokens_in,
                "tokens_output": tokens_out,
                "elapsed_s": round(elapsed, 2),
            }

        except anthropic.RateLimitError as e:
            last_error = f"RateLimitError"
            wait = RATE_LIMIT_WAIT * (attempt + 1)
            print(f"\n  [{record.get('name')}] RateLimitError — retry {attempt+1}/{MAX_RETRIES} in {wait:.0f}s")
            await asyncio.sleep(wait)
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as e:
            last_error = f"{type(e).__name__}"
            wait = 30.0 * (attempt + 1)
            print(f"\n  [{record.get('name')}] {last_error} — retry {attempt+1}/{MAX_RETRIES} in {wait:.0f}s")
            await asyncio.sleep(wait)
        except Exception as e:
            elapsed = time.monotonic() - t0
            return {
                "status": "failed",
                "original": record,
                "error": f"{type(e).__name__}: {e}",
                "tokens_input": tokens_in,
                "tokens_output": tokens_out,
                "elapsed_s": round(elapsed, 2),
            }

    elapsed = time.monotonic() - t0
    return {
        "status": "failed",
        "original": record,
        "error": f"exceeded {MAX_RETRIES} retries ({last_error})",
        "tokens_input": tokens_in,
        "tokens_output": tokens_out,
        "elapsed_s": round(elapsed, 2),
    }


async def _agentic_loop(
    client: anthropic.AsyncAnthropic,
    http_session: aiohttp.ClientSession,
    system: list,
    messages: list,
) -> tuple[dict, int, int]:
    """Run until end_turn. Returns (parsed_json, tokens_in, tokens_out)."""
    tokens_in = tokens_out = 0
    loop_msgs = list(messages)

    for _ in range(6):  # hard cap: max 5 tool calls + 1 final response
        response = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            temperature=0,
            system=system,
            messages=loop_msgs,
            tools=[WEB_SEARCH_TOOL, WEB_FETCH_TOOL],
            extra_headers={"anthropic-beta": "web-search-2025-03-05"},
        )

        tokens_in  += response.usage.input_tokens
        tokens_out += response.usage.output_tokens

        if response.stop_reason == "end_turn":
            text = _extract_text(response.content)
            return _parse_json(text), tokens_in, tokens_out

        if response.stop_reason == "pause_turn":
            loop_msgs = loop_msgs + [{"role": "assistant", "content": response.content}]
            continue

        if response.stop_reason == "tool_use":
            loop_msgs.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if block.name == "web_fetch":
                    content = await _do_fetch(block.input.get("url", ""), http_session)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": content,
                    })
                elif block.name == "web_search":
                    # Server-side — Anthropic runs it; send minimal ack
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "Search executed.",
                    })
            if tool_results:
                loop_msgs.append({"role": "user", "content": tool_results})
            continue

        break

    raise RuntimeError(f"Loop ended without end_turn (stop_reason={getattr(response, 'stop_reason', '?')})")


def _extract_text(content: list) -> str:
    return "\n".join(b.text for b in content if hasattr(b, "type") and b.type == "text")


def _parse_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{[\s\S]+\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Cannot parse JSON from: {text[:400]}")
