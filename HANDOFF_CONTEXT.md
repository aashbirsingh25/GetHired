# GetHired — Context Handoff

This document brings a new AI assistant up to speed on the GetHired project and the recent work, so you can continue without re-discovering everything.

---

## The user
- **Aashbir Singh** — a fresher (0 years experience) targeting **entry-level software roles in India** (Gurugram / Bangalore / Delhi NCR / Noida / remote).
- **Non-technical.** Explain in plain language, one step at a time. He may cancel long-running commands — keep them short.
- GitHub: `aashbirsingh25`. Local git identity for this project: **`Aashbir Singh <singhaashbir1234@gmail.com>`** — always use this, never a different identity, never `--global`.

## What GetHired is
A self-hosted, **zero-recurring-cost** job discovery engine. It autonomously scans company career pages and job boards every few hours, scores each posting against his resume with LLMs (with transparent explanations), and serves a **freshness-first ranked feed**. Core mission: surface the newest relevant fresher jobs ASAP.

- **Freshness rules:** feed shows last **24 h** only; store retains **72 h** (tracked jobs protected); 30-day tombstones stop pruned jobs resurrecting; `posted_date` beats `first_seen` for recency.
- **Hard rule: no fake data, ever.** If a source can't be verified it doesn't enter the store.
- **Scale now:** ~7,600 jobs, ~283 companies, ~60 fresher-active companies (~21%). Goal: grow toward 1,000 companies / 75% fresher-active via autonomous discovery + a watchlist promote loop.

## Architecture (one-liners)
- `app.py` — Flask app + APIs. **Sets `OMP_NUM_THREADS=1` first** (mandatory on macOS or the embedding stack segfaults). Port 5050.
- `pipeline.py` — filtering + scoring gates (experience=0, India/remote location, seniority blocks like SDE-2/3, recency, evidence cap).
- `scan_coordinator.py` — parallel scan workers with a **sequential browser lane** (Playwright must run on one owning thread; workers pass `allow_browser=False` and defer browser work).
- `browser_scanner.py` — Playwright career-page scraping + per-ATS extractors (Greenhouse, Lever, Workday, SuccessFactors, …).
- `company_discovery.py` — autonomous employer discovery every 2 h; feeds a **watchlist** with park → reprobe → promote lifecycle.
- `hybrid_scorer.py` + `llm_router.py` — tiered scoring: **Gemini (10 keys) + Groq → Ollama qwen2.5:7b → semantic vectors (mpnet + FAISS) → rule-based**. Real quota accounting (daily rollover at midnight US-Pacific = 12:30 PM IST, headroom-ordered provider selection, escalating backoff). Note: Claude/OpenAI scorer files exist but their keys are placeholders — those tiers are NOT active.
- `job_deduplicator.py` — company-prefix bucketed dedup (fixed a quadratic bug: 2,349s → 16.7s).
- `store_pruner.py`, `job_liveness_checker.py` — retention/tombstones and dead-posting detection (closed jobs → `closed_jobs.json`, one writer).
- `linkedin_detail_enricher.py` — logged-out only, 1 req/20s, 60/day, stops on authwall.
- `hardened_fetch.py` — Scrapling TLS-impersonation fetch (cracked Cognizant/TCS bot walls; its stealth *browsers* crash on macOS — retry on Linux at deployment).
- `fetchers/` — Adzuna, Jooble, Internshala, Freshersworld, Cutshort, Naukri (Apify, budget-capped $4.50/mo, 1 run/20h), Indeed (JobSpy in a **quarantined venv** `.venv-jobspy` — its deps break sentence-transformers, so it's called via subprocess).
- `static/index.html` — the **entire dashboard** (vanilla JS + one hand-rolled CSS block; NOT Tailwind despite old docs).

## Environment facts
- Repo: on the original machine at `/Users/surleenk/Desktop/aashbir/GetHired`. GitHub remote `https://github.com/aashbirsingh25/GetHired.git`.
- Main venv `.venv` (Python **3.12**). Quarantine venv `.venv-jobspy`.
- Run: `OMP_NUM_THREADS=1 .venv/bin/python app.py` → localhost:5050 (~15s startup). Kill: `kill $(lsof -ti :5050)`.
- Ollama: run as plain background process `ollama serve &` (brew services was broken), model `qwen2.5:7b`.
- Secrets in `.env` (gitignored, chmod 600): `GEMINI_API_KEYS` (10), `GROQ_API_KEY`, `APIFY_API_TOKEN`, `ADZUNA_APP_ID/KEY`, `JOOBLE_API_KEY`.
- `requirements.txt` is **incomplete** — also needs `playwright`, `scrapling`, `pytz`, `lxml`.
- Tests: `OMP_NUM_THREADS=1 .venv/bin/python test_ui_e2e.py` (expect `ALL PASS`); unit tests `test_scan_scheduler.py test_experience_filter.py test_phase3_stability.py`.

## Git workflow used
- Commit as the identity above. Push is standing-approved (GitHub PAT in macOS keychain).
- Merge to master routinely: `git fetch . fix/pending-scoring-bug:master && git push origin master fix/pending-scoring-bug`. (The working branch is `fix/pending-scoring-bug`; master tracks it.)
- **Never `git add -A` blindly** (a 104MB tmp file was once committed by accident; `*.tmp_*` is now gitignored). Stage specific files.

## Recent session work (most recent first)
1. **GitHub profile README** (separate repo `aashbirsingh25/aashbirsingh25`, on `main`): redesigned several times, landed on a **dark terminal-window theme** — header is a macOS terminal (`$ whoami` → gradient name), project cards are mini terminals, footer is a git-graph "see you in the next commit" sign-off, tech stack is one row of 10 shield badges. Hand-drawn SVG mascots were tried and **removed** (user didn't like them — character art is a known weak spot; use ready-made illustrations if revisited).
2. **GetHired README rewritten** to match the real system (removed the fictional Claude/OpenAI tiers and the "Tailwind" claim; documented freshness, autonomy, real scoring tiers, setup, `.env.example` added).
3. **UI empty/error states**: hamster-in-a-scene illustrations were built then **replaced with plain text** ("Nothing here" / "Something went wrong") at user request.
4. Earlier this session: freshness-first pivot, Apify Naukri fetcher, JobSpy quarantine, Scrapling adoption, dedup perf fix, feed caching (~6ms), quota system rebuild, tiered feed threshold, evidence cap. All tests green.

## THE current task
The user is **moving GetHired to a second laptop** to deploy it from there. He's using **Antigravity** (an automation agent) on that laptop to: pull latest from GitHub, install missing dependencies, and run it. See **`LAPTOP_SETUP.md`** in the repo for the exact instructions handed to Antigravity. The one thing that can't be automated is copying the `.env` file (secrets aren't in git).

## What's left after the move
- **Deployment decision** (the big open item): laptop-as-server (~15 min, recommended) vs $5 VPS vs Oracle free tier — user hasn't chosen.
- On Linux (if a server is used): retry Scrapling stealth browsers (they crash on macOS), make Ollama a proper service.
- Ongoing autonomous background work: grow companies toward 75% fresher-active, watchlist promotions, weekly reviews.
- User's own to-dos: add more free LLM keys (Cerebras/Mistral/SambaNova/Cohere suggested — free, no card), apply to top feed jobs.

## Tone reminder for whoever continues
Plain language, short steps, no jargon dumps. Verify with screenshots/tests before claiming success. Never invent data or oversell — this project's whole ethos is honesty (real jobs, real scores, real capabilities).
