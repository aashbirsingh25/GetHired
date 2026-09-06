# GetHired

**A self-hosted, zero-recurring-cost job discovery engine that hunts while you sleep.**

GetHired autonomously scans company career pages and job boards, scores every posting against your resume using LLMs, and serves a ranked feed of fresh, relevant openings — built for one very specific mission: landing an entry-level software role in India, fast.

> Freshness first: the feed only shows jobs posted in the last **24 hours**. Anything older than **72 hours** is pruned. Stale listings are the enemy.

---

## Why

Job boards are noisy, slow, and full of dead or senior-level postings. Checking 300 company career pages by hand is impossible. GetHired does it every few hours, filters ruthlessly for **0-experience-friendly roles in Gurugram / Bangalore / Delhi NCR / Noida / remote**, explains *why* each job matches, and throws away anything stale, dead, or mislabeled.

## How it works

```
 13 job sources                    autonomous discovery
 (career pages, boards, APIs)      (finds new companies every 2h)
        │                                   │
        ▼                                   ▼
 ┌─────────────────────────────────────────────────┐
 │  Scan coordinator — parallel workers,           │
 │  sequential browser lane, per-company dormancy  │
 └─────────────────────────────────────────────────┘
        │
        ▼
 validation → dedup (company-bucketed) → tombstone check
        │
        ▼
 filters: experience (0 yrs) · location (India/remote) ·
          seniority (SDE-2/3, Engineer III blocked) · recency
        │
        ▼
 ┌─────────────────────────────────────────────────┐
 │  Tiered scoring engine                          │
 │  1-2. LLM (Gemini ×10 keys, Groq) w/ quotas     │
 │  3.   Local LLM (Ollama qwen2.5:7b)             │
 │  4.   Semantic vectors (mpnet + FAISS)          │
 │  5.   Rule-based keyword fallback               │
 └─────────────────────────────────────────────────┘
        │
        ▼
 ranked feed (24h window) → web dashboard → apply
```

## Key features

**Collection**
- Playwright-driven career-page scraping with per-ATS extractors (Greenhouse, Lever, Workday, SuccessFactors, and more)
- Job board fetchers: Adzuna, Jooble, Internshala, Freshersworld, Cutshort, Naukri (via Apify, budget-capped), Indeed (via JobSpy in a quarantined venv)
- TLS-impersonation fetching (Scrapling) to reach bot-walled sites
- LinkedIn description enrichment — logged-out only, strictly rate-limited (1 req / 20 s, 60/day)

**Autonomy**
- Company discovery worker finds and verifies new employers every 2 hours across 18 categories
- Watchlist with park → reprobe → promote lifecycle for companies that aren't hiring freshers *yet*
- Zero-yield sweeps demote dead sources; dormancy backs off quiet companies
- Job liveness checker marks dead postings closed; closed jobs never resurface

**Freshness**
- Feed shows last 24 h only; store retains 72 h (tracked jobs protected)
- 30-day tombstones stop pruned jobs from resurrecting on rescan
- `posted_date` always beats `first_seen` for recency

**Scoring**
- Tiered engine with real quota accounting: daily rollover at midnight US-Pacific, headroom-ordered provider selection, escalating backoff
- Score verification: ambiguous scores get a second opinion from a different model
- Evidence cap: stub descriptions can't score above 60 ("limited info" badge)
- Transparent explanations — every score says why

**Dashboard**
- Single-page app (vanilla JS + hand-rolled CSS): ranked feed, search with filters, application tracker (applied / referred / shortlisted / apply-later / viewed / saved), insights, quota pulse, resume upload
- Feed API answers in ~6 ms thanks to mtime-keyed caching

## Running it

```bash
# 1. main environment (Python 3.12)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium

# 2. secrets — copy and fill (never commit this)
cp .env.example .env    # GEMINI_API_KEYS, GROQ_API_KEY, APIFY_API_TOKEN,
                        # ADZUNA_*, JOOBLE_API_KEY

# 3. optional: local LLM tier
ollama serve &
ollama pull qwen2.5:7b

# 4. optional: Indeed fetcher (isolated — its deps conflict)
python3 -m venv .venv-jobspy && .venv-jobspy/bin/pip install python-jobspy

# 5. run
.venv/bin/python app.py     # dashboard on http://localhost:5050
```

macOS note: `OMP_NUM_THREADS=1` is set first thing in `app.py` — removing it segfaults the embedding stack.

## Testing

```bash
OMP_NUM_THREADS=1 .venv/bin/python test_ui_e2e.py            # 40+ Playwright checks
OMP_NUM_THREADS=1 .venv/bin/python -m pytest test_scan_scheduler.py test_experience_filter.py test_phase3_stability.py
```

## Project layout

```
app.py                     Flask app + API
pipeline.py                filtering & scoring gates
scan_coordinator.py        parallel scan orchestration
browser_scanner.py         Playwright career-page scraping + ATS extractors
company_discovery.py       autonomous employer discovery
hybrid_scorer.py           tiered scoring engine
llm_router.py              provider rotation, quotas, backoff
job_deduplicator.py        company-bucketed dedup
store_pruner.py            72h retention + tombstones
job_liveness_checker.py    dead-posting detection
linkedin_detail_enricher.py  description enrichment (rate-limited)
hardened_fetch.py          TLS-impersonation fetch helper
fetchers/                  per-board fetchers
static/index.html          the entire dashboard
```

## Ground rules this project follows

- **No fake data.** If a source can't be verified, it doesn't enter the store.
- **Polite scraping.** Rate limits, backoff, dormancy; LinkedIn only logged-out and heavily throttled; one account per provider.
- **Zero recurring cost.** Free LLM tiers with honest quota tracking; the only paid call (Apify) is capped at $4.50/month and gated to one run per 20 hours.

## Status

Actively developed and in daily use. Current store: ~7,600 jobs across ~283 companies, with autonomous discovery growing coverage toward 1,000 companies.
