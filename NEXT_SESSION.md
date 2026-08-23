# NEXT SESSION — start here

Written 2026-08-24 03:30 IST, at the end of a long build session.
Read this together with `.kiro/steering/product-context.md`,
`.kiro/steering/orchestrator-mode.md` and `PROGRESS_LOG.md`.

---

## 0. FIRST FIVE MINUTES (always, in this order)

1. Git safety check inside the repo:
   `git config user.name && git config user.email && git remote -v`
   Expect **Aashbir Singh / singhaashbir1234@gmail.com** (set locally in
   `.git/config`) and remote **only** `aashbirsingh25/GetHired`.
2. `git status` and `git -P log --oneline -n 5` — confirm the working tree is
   clean and we are on branch **fix/pending-scoring-bug**.
3. Read `PROGRESS_LOG.md` (bottom ~100 lines) for the latest state.
4. Start the app if it is not running:
   `cd ~/Desktop/aashbir/GetHired && .venv/bin/python -u app.py > /tmp/gethired_server.log 2>&1 &`
   It serves on **http://localhost:5050** (not 5000).
5. If ≥7 days have passed since the last review, do the **WEEKLY REVIEW**
   (section 2) before anything else.

---

## 1. WHAT IS RUNNING BY ITSELF (no action needed)

- **Scans** every 6h across 275 companies, two lanes (parallel API lane +
  sequential browser lane), with active/dormant and fresher-aware tiering.
- **12 job sources**: company career pages, Internshala, Shine,
  Freshersworld, LinkedIn (222-query fresher sweep), Cutshort, RemoteOK,
  Remotive, Adzuna, Jooble (+ Indeed/Naukri stubs parked).
- **LLM scoring**: 12 Gemini keys + 1 Groq in `.env` (gitignored). Cheap
  local pass over the whole store, then a refinement pass that re-scores
  every feed candidate through Gemini. Background work yields to scoring
  via `LLMRouter.has_headroom`.
- **Autonomous company discovery** every 6h: proposes (mined postings + LLM
  by rotating category) → verifies live against ATS APIs → admits only
  companies with India **and** fresher-eligible openings → logs every
  decision. Non-admitted-but-real companies go on `company_watchlist.json`
  and are re-probed every cycle (never deleted).
- **WEEKLY_REVIEW.md** is regenerated each discovery cycle for the review
  ritual below.

NOTE: none of this runs while the laptop is off. If it was off for 3 days,
expect the first cycles after startup to catch up.

---

## 2. WEEKLY REVIEW RITUAL (the "review thing")

Trigger: user says **"do the weekly review"**, or ≥7 days since the last one.

1. Read `WEEKLY_REVIEW.md` (auto-generated: 7-day activity, adds,
   promotions, rejection reasons, near-misses, current taught rules).
2. **Spot-check the "near-misses"** — companies with real India hiring but
   no fresher role yet. These are the worker's riskiest calls. Verify 3-5 of
   them live (probe their ATS, look for fresher titles).
3. **Verify a sample of that week's ADDED companies**: do they actually scan
   through `BrowserScanner`, and do they really have fresher-eligible roles?
4. **Teach the worker** by editing `discovery_rules.json`:
   - `blocklist_names` — confirmed junk, never propose again
   - `force_watch_names` — keep watching even if they look dead
   - `min_fresher_to_admit` — raise if quality is slipping
   - `notes` — dated reasoning (the worker logs these)
5. Report to the user in the standard format (WHAT I DID / WHAT I VERIFIED /
   WHAT YOU SHOULD CHECK / WHAT'S NEXT) and append to `PROGRESS_LOG.md`.

NON-NEGOTIABLE: never delete a verified-real company because it has no
fresher opening today. A company can post tomorrow.

---

## 3. WHAT'S LEFT — IN PRIORITY ORDER

### A. Needs the user (blocking, ask early in the session)
1. **MERGE SIGN-OFF (most overdue).** ~100 verified commits sit on
   `fix/pending-scoring-bug`; `master` is still the pre-ChatGPT baseline.
   Ask the user to look at the feed, then get explicit sign-off, then merge
   (product-context.md Section 6 rule 6). Also fold in the trivial
   `coderabbit-review` branch. Re-audit stance: two serious regressions were
   already found in the debug branch, so review before merging, don't trust.
2. **UI direction.** Lane A (honest feedback: upload errors, transparent
   empty-feed breakdown) is DONE. Lanes B (targeted visual fixes) and C
   (full redesign) need the user's complaints list / decision. Per
   product-context 8B: present 2-3 directions, one page at a time.
3. **Apify** — account was DISABLED (all 10 tokens 401, verified). Likely
   multi-account anti-abuse. Until a working token exists, Naukri, Indeed,
   Glassdoor, TCS and Infosys career sites stay unreachable. Do not create
   more accounts.
4. **User's own company list** — he offered to supply one. The verification
   pipeline (`scratch/ats_probe.py`, or `company_discovery.verify_candidate`)
   is ready to run it through the fresher gate.

### B. My queue (no user input needed)
5. **Threshold recalibration.** `min_match_score` (currently 55) was
   calibrated on cheap local-tier scores. Once the LLM refinement pass has
   finished, re-measure the score distribution and re-tune. Check
   `resume_store.json → rescore_status` first.
6. **Sweep the remaining ~85 zero-yield companies** with the LLM page
   learner (batches of ~12-14; run when scoring is idle or the headroom
   guard will skip). Pattern so far: most are behind bot protection
   (Akamai/Cloudflare) or on unsupported enterprise ATS platforms.
   Confirmed unreachable: TCS, Infosys, Uber India, Autodesk, Zomato,
   Netflix India, Morgan Stanley India.
7. **Company-list growth toward 1000** using live web search per category
   (NOT memory) + the fresher gate. Current: 275 companies, **~17%
   fresher-active** vs the 75% target (see `/api/company-health` and the
   Insights card). Best-yielding categories measured so far: quant/HFT
   (Jump 44 fresher roles, IMC 31, Point72 19), mass IT services, GCC
   graduate programmes. Worst: big global SaaS (Databricks 3 of 501).
8. **More ATS extractors** if a detected platform recurs. Built so far:
   Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Keka, TurboHire,
   Oracle HCM. Ruled out: Darwinbox (portals need login), Zoho Recruit,
   TimesJobs, Foundit, Instahyre (all blocked/no public data).
9. **Small fixes queued**: Microsoft job titles have location text glued on
   (scraper bug); `vision_fallback_parser.py` still not wired into the live
   scan loop; audit the threshold-optimizer / feedback-learning / Insights
   data accuracy once real usage data exists.

### C. Later
10. **Oracle Cloud deployment** (~2-4h + the Always-Free capacity lottery).
    Expect more bot-blocks from a datacenter IP, so Apify matters more there.
    Keys must move to env vars on the VM. Do this only after the UI and
    merge are settled, otherwise every change needs redeploying.

---

## 4. HARD RULES (unchanged)

- Never touch git config beyond product-context Section 0. Never `--global`.
  The laptop's global identity belongs to the user's sister (Amazon) and must
  never appear on a GetHired commit.
- Never use Amazon-internal tooling for this project.
- Never merge to `master` without explicit sign-off.
- Never re-enable `_generate_career_jobs` or insert fake/placeholder data.
- LinkedIn: public logged-out listings only. Never the user's account,
  never auto-apply, never messaging.
- Never report something as verified without having run it this session.
- Recurring trap, hit twice: **truncated identifiers** (an LLM hint gave
  `/sites/CX_` instead of `/sites/CX_1`; a detector truncated a UUID).
  Always extract full IDs from the live page before storing them.

---

## 5. CURRENT NUMBERS (2026-08-24 03:30 IST)

- 275 companies · 12 sources · ~11.6k jobs in store
- fresher-active companies: 47 (~17%), target 75%
- Gemini usage: ~265 of ~18,000 daily calls (1.5%) — quota is not the
  constraint; per-minute limits are
- Jooble lifetime budget: 4 of 400 calls used
- Branch `fix/pending-scoring-bug`, ~100 commits, working tree clean
