<div align="center">

# Washon Investment Suite

**AI-Powered Investment Intelligence for Early-Stage Startups**

*Automate investor discovery — from business plan to matched investors to personalized outreach*

[![Live Demo](https://img.shields.io/badge/Live%20Demo-Available-brightgreen?style=for-the-badge)](https://web-production-3970e.up.railway.app/)
[![Python](https://img.shields.io/badge/Python-Flask-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://flask.palletsprojects.com/)
[![PostgreSQL](https://img.shields.io/badge/Database-PostgreSQL-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://www.postgresql.org/)

</div>

---

## Overview

Washon Investment Suite is an intelligent investor-matching platform for early-stage startups. It automates the process of finding relevant investors by parsing business plans with AI, running a multi-tier matching engine against a curated investor database, and generating personalized outreach emails — all in a single closed-loop workflow.

---

## Features

| Module | Description |
|--------|-------------|
| **BP Parser** | Upload a pitch deck (PDF/DOCX) and extract a structured investment profile via Claude AI |
| **Investor Database** | Browse 485 curated VC & PE investors with 32 fields per record |
| **Pipeline Manager** | Track investor outreach across 6 stages with visual status management |
| **Analytics** | Save and revisit past analyses with full match history |

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                  Washon Investment Suite                  │
│                                                          │
│  ┌───────────┐    ┌──────────────────┐    ┌──────────┐  │
│  │ BP Parser │───▶│  3-Tier Matching  │───▶│  Email   │  │
│  │ (Claude)  │    │      Engine       │    │  Writer  │  │
│  └───────────┘    └──────────────────┘    └──────────┘  │
│        │                   │                    │         │
│        ▼                   ▼                    ▼         │
│   pypdf / docx      Voyage-3 Embeds        Exa API       │
│                     + Score Engine         Real-time      │
│                     (1,137 lines)          Research       │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │       PostgreSQL · 12,102 investor records         │  │
│  │       SEC IAPD (11,307) + Exa VC (795)             │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

---

## 3-Tier Matching Engine

The matching algorithm runs 35 functions across three sequential layers to surface genuinely relevant investors — not just superficially similar ones.

```
Tier 1 · Hard Filters     →  Investment stage + Jaccard sector similarity
                              + AUM range + Geography
                              (any miss = eliminated)
                              ↓
Tier 2 · Soft Scoring     →  Business Model × 0.25 + Traction × 0.30
                              + Team × 0.20 + Lead Capacity × 0.10
                              + Geography × 0.15
                              ↓
Tier 3 · Semantic Match   →  Voyage-3 cosine similarity
                              (BP narrative ↔ Investor Thesis)

Final Score = Sub-F1 base × (0.60 + Tier2 × 0.80) × confidence coefficient
```

Sub-F1 acts as the score anchor; Tier 2 is a modulation coefficient (range 0.60–1.40), ensuring no single dimension dominates the result. All intermediate scoring variables are returned to the frontend for full **decision explainability**.

---

## Investor Database

12,102 total records built from two parallel ingestion tracks:

**Track 1 — SEC IAPD**
- Source: SEC's official IAPD database (23,280 raw records)
- Filtered by US address + private fund eligibility (Q8A1/Q8A2 fields) + non-zero AUM
- Result: **11,307 compliant US institutional investors** · AUM coverage 90% · Median AUM $475M

**Track 2 — Exa Semantic Search**
- Targets early-stage VC coverage missing from SEC data
- Queried across 8 verticals: AI, semiconductors, healthcare, fintech, energy, and more
- Result: **795 sector-focused VC records** after cleaning

**4-Layer Data Cleaning Pipeline:**

```
Layer 1 · Rule-based filter      Regex/keyword removal of noise (geonames, person names, stats)
Layer 2 · Name normalization     Strip numeric suffixes, remove job-title concatenations
Layer 3 · AI validation          Groq (Llama 3.1 70B) per-record classification (27% deletion rate)
                                  + manual review to recover false positives
Layer 4 · Exa re-enrichment      Re-query with clean names → extract portfolio, key people, contacts
```

---

## Email Generator

Personalized investor outreach emails are generated via a **4-layer pipeline**:

1. **SSE streaming** — timestamped step logs pushed to the frontend in real time; no blank-screen wait
2. **Live research** — Exa API fetches the investor's recent portfolio and partner quotes before generation
3. **Two-stage self-critique** — Stage 1 drafts the email; Stage 2 scores it across 6 dimensions (opening hook, social proof, traction clarity, CTA, etc.)
4. **Retry loop** — if the score is below threshold, the critique is fed back to the generator for rewrite

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Web Framework** | Flask + Jinja2 · Gunicorn (4 workers × 2 threads) |
| **Database** | PostgreSQL (production) / SQLite (local) — single env var switch, zero code changes |
| **Auth** | Flask-Login + Flask-Bcrypt · bcrypt hashing · session persistence |
| **AI / LLM** | Anthropic Claude · Voyage AI Voyage-3 (vector embeddings) |
| **Search & Data** | Exa API · SEC IAPD API · pypdf · python-docx |
| **Deployment** | Railway · Sentry SDK · health check `/` (30s timeout) · auto-restart (max 3×) |
| **Frontend** | Vanilla JS + HTML/CSS · Canvas particle animation · SSE |

---

## Engineering Notes

**Security & Multi-tenant Isolation**
- All user data queries are filtered by `user_id` at the ORM layer — enforced at the database level
- Passwords stored with bcrypt hashing; connection strings scrubbed before logging
- `_PUBLIC_ENDPOINTS` frozenset controls public/private access boundaries

**Defensive Error Handling**
- 87 `try/except` blocks in the matching engine; all field reads catch `(KeyError, TypeError)`
- LLM call errors mapped to appropriate HTTP status codes; messages truncated to 300 chars to prevent information leakage
- `finally` blocks guarantee uploaded temp files are always cleaned up

**Observability**
- Structured log prefixes (`[DB]`, `[migration]`) for production log search
- SSE progress logs in `[HH:MM:SS]` format — every AI processing step is visible in the UI
- Sentry SDK integrated via environment variable for automatic error reporting

---

## Project Stats

```
~15,916 lines of Python   ·   36 files   ·   28 HTTP routes
 12,102 investor records  ·   32 fields per investor   ·   9 investor types
  3-tier matching engine  ·   35 functions   ·   30+ sector synonym rules
```

---

## License

© Washon Capital. All rights reserved.

This repository is for showcase purposes only. The source code, data, and algorithms contained herein are proprietary and may not be copied, modified, distributed, or used without explicit written permission.

---

## Access

This platform is not publicly open-sourced. To request access or discuss collaboration, please reach out via the live platform or contact us directly.

[![Live Platform](https://img.shields.io/badge/Visit-Live%20Platform-blue?style=for-the-badge)](https://web-production-3970e.up.railway.app/)
