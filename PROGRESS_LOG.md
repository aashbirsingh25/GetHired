# GetHired — Progress Log

One entry per task. Newest first. This is cross-session memory: read it at the
start of every session before picking a task.

---

## 2026-08-22 — Fix PENDING-scoring feed flood (Section 3 bug)

- **Branch:** `fix/pending-scoring-bug` (off `debug/gethired-stability`)
- **Status:** DONE — needs user live review before merge to master.

**What was done**
- `pipeline.py` `execute_authoritative_pipeline`:
  - Step 8: a job is now treated as PENDING if it has no valid cached match
    OR its cached `match.resume_version_hash` differs from the current
    resume's `version_hash` (the stale-resume case that was previously
    served with a stale score). PENDING is only applied when an active
    resume exists (`has_resume`/`version_hash`); with no resume, match-less
    jobs flow through the feed unchanged (as before).
  - Step 9: removed the bug where PENDING jobs bypassed `min_match_score`.
    PENDING jobs are now split out into a separate `pending_jobs` list and
    excluded from the ranked feed (option a, per user), so they no longer
    flood the feed at score 0 / sort to the top.
  - Return payload now includes `pending_jobs` and `metrics.pending`.
  - Applied-job exclusion also applied to `pending_jobs`.
- `app.py` `/api/jobs`: response now includes `pending_count` for a
  "N jobs still being scored" transparency indicator.
- Tests updated to reflect option (a) instead of the old buggy
  PENDING-in-feed expectation: `test_phase3_stability.py` (test_03, test_10,
  new test_10b for stale-hash), `test_phase4_stability.py` (test_06).

**What was verified, and how**
- Ran affected unit tests via the repo's local venv (`.venv`):
  `test_phase1_stability` + the 4 updated pipeline tests +
  `test_suite` test_05/test_12 → 11 tests, all OK.
- Live end-to-end via Flask `app.test_client()` hitting `GET /api/jobs`
  with a controlled store (1 current-hash job, 1 stale-hash job, 1
  never-scored job) and an active resume + `min_match_score=50`:
  observed HTTP 200, ranked feed = ["Software Engineer" score 88] only,
  `pending_count=2`, `metrics.pending=2`, `metrics.filtered=1`. The stale
  and never-scored jobs were correctly held out of the feed.
- Negative case (no resume): `pending_count=0`, all 3 jobs flow into the
  feed — confirms no regression to browse-without-resume.

**Notes / discrepancies with product-context.md**
- The doc (Sections 3 & 5) states "the background rescorer does not exist."
  It DOES exist: `_async_rescore_jobs` in `app.py`, triggered on resume
  upload, respects `resume_version_hash`, tracks `rescore_status`. The real
  bug was the two pipeline issues above, not a missing rescorer. Fixed the
  pipeline rather than building a duplicate rescorer.
- Chose the pipeline fix over "restore synchronous scoring" because the
  async design is deliberate and enforced by `test_03` (< 0.5s, no
  synchronous heavy scoring). Synchronous scoring would have broken it.

**Environment note (not committed)**
- Created a local `.venv` and installed `requirements.txt` + `playwright`
  (playwright is imported by `browser_scanner.py` but missing from
  requirements.txt). Added `.venv/ venv/ env/` to `.gitignore`.
  `sentence-transformers` installed cleanly in this venv — the Section 5
  "not loading" issue may be a separate interpreter-mismatch task, not
  addressed here.

**Not done (waiting on user)**
- No merge to master. Awaiting live verification: upload/change a resume in
  the running app and confirm jobs get real scores (not permanent PENDING),
  and that the "N still scoring" indicator behaves.

## 2026-08-22 — Fake test data polluted live verification (branch: fix/pending-scoring-bug)

User's live check of the PENDING fix showed 2 dummy jobs ("Python Dev/Co A",
"Flask Dev/Co B") both opening careers.google.com. Investigation found:
- test_phase3_stability test_04 had written fixture jobs into the REAL
  jobs_store.json; restore failed because the file didn't exist pre-test on
  this machine, so tearDown had nothing to restore. resume_store.json was
  similarly overwritten with a fake test resume ("Sample text", Python/Flask)
  — the user's real resume, if uploaded, was lost and must be re-uploaded.
- app.py apply-direct had a hardcoded careers.google.com fallback URL;
  index.html had a flipkartcareers.com fallback. Both removed (422 + toast).
- A stale Flask server (from before the fix commits) was still running on
  port 5050 — killed and restarted with current code. NOTE: app runs on 5050.

Verified this session: 12/12 phase3 tests pass with real store intact after;
live server GET /api/jobs = 0 jobs/0 pending on clean store; POST
apply-direct on URL-less job = HTTP 422, no fake URL, no application record.
Polluted data backed up to scratch/pollution_backup_20260822/.

Status: done (commit 973b1a2). PENDING-fix live verification still owed by
user, now against real data: re-upload real resume, run a scan, check scores.
The fake-job generator remains disabled (verified: zero callers).

## 2026-08-22 (later) — Resume upload 500: missing pdfplumber/python-docx

User's resume upload silently failed (UI showed nothing; server log showed
POST /api/resume 500, ModuleNotFoundError: pdfplumber). resume_parser.py
needs pdfplumber + python-docx; neither was in requirements.txt. Installed
both into .venv, added to requirements.txt (commit follows), restarted
server. Verified parse_resume on the real PDF: 3869 chars, 17 skills.
NOTE: product-context.md Section 5 claims feedparser and
sentence-transformers are broken — on THIS Mac both import fine (those
notes were from the Windows laptop). Naukri's HTTP 400 is a separate issue.
Status: done. User still owed: re-upload resume via UI, run scan, verify
real scores.

## 2026-08-22 (night) — "Unresponsive upload button" was a server segfault

User reported upload button doing nothing. Actual cause chain: the upload
request crashed the entire Flask process (SIGSEGV) mid-request, so the
browser got no response — looked like a dead button. Root cause: torch
(sentence-transformers) + faiss each bundle libomp.dylib on macOS; using
both in one process segfaults. Deterministic repro built (6/6 crashes).
KMP_DUPLICATE_LIB_OK did NOT fix it (3/3 crashes). OMP_NUM_THREADS=1 set
at top of app.py DID (8/8 clean incl. threaded). Also explains the earlier
"Unexpected EOF" pdfplumber error (truncated state after a crashed
process) and servers repeatedly found dead.
Verified live: real resume uploaded via POST /api/resume -> HTTP 200,
server alive, resume_store.json has 17 skills, hash 221a39b573dcb9a1,
rescore completed. Resume upload is DONE — user must still run a scan and
verify real job scores.

## 2026-08-23 (00:20) — Real scanner restored; "no jobs found" root-caused

"No jobs" had 3 stacked causes, all fixed (commit on fix/pending-scoring-bug):
1. debug branch removed master's startup thread for background_scanner_loop
   → the REAL scanner (ScanCoordinator/BrowserScanner) never ran at all.
   fetch_career_pages only re-reads jobs_store.json (circular no-op "scan").
   ** This is a second serious regression from the ChatGPT debug branch that
   product-context.md Section 2 did NOT catch — its browser_scanner.py
   review was code-level, missing that the caller was disconnected.
   Re-audit that branch before merging to master. **
2. /api/company/<id>/rescan: indentation bug (debug branch) = NameError.
3. Scanned jobs stay match=None forever (rescorer only ran on resume
   upload). Now scans trigger rescore on completion; rescorer merges scores
   by id into fresh store (was clobbered by concurrent scan writes - lost
   568 scores once).
Also: playwright chromium installed (~95MB, one-time), needed by scanner.
Verified live: Razorpay rescan = 25 real jobs via greenhouse_api; startup
scan reached 568 jobs @ 16/212 companies before restart; rescorer 568/568.
Full scan ~1h; auto-rescore fires at end. User verification of scored feed
pending scan completion.
Runtime files (companies.json, pattern_store.json, trial_periods.json) are
git-tracked and churn during scans — left uncommitted, consider gitignoring
later.

## 2026-08-23 (01:00) — Job board sources: Cutshort added; Naukri/Indeed/Glassdoor verdicts

Cutshort fetcher built + wired (commit above), verified standalone: 25
jobs/location via embedded __NEXT_DATA__ JSON, no key needed. Activates on
next server restart (NOT restarted - overnight scan running, 700 jobs so far).
Naukri: RSS dead (400), API wants captcha (406), site Akamai-blocks headless
browsers. Indeed: publisher API closed to new signups. Path for both =
existing Apify integration; USER ACTION: create free apify.com account, put
token in config.json apify.api_token (spend caps already coded: $1.5/day,
$5/mo = free tier). Glassdoor: skipped (bot-protected, redirects to career
pages we already scan). LinkedIn: manual only, hard boundary, user reminded.

## 2026-08-23 (01:05) — RemoteOK + Remotive fetchers added

Free public APIs, no keys. India-eligibility filter (these boards are
global/US-heavy). Verified via full _fetch_all_sources_parallel: remoteok 7,
remotive 8, cutshort 25. Active at next restart.
QUEUED for next session: Adzuna (needs free key - user), YC Work at a
Startup + Instahyre + Wellfound (probe first), Naukri/Glassdoor via Apify
(blocked on user's Apify token). LinkedIn: user asked to relax the rule;
REQUIRES explicit daytime confirmation "update the LinkedIn rule" before
implementing (Apify public-listings only, never user's account) + update
product-context.md boundary text. Multi-account key rotation on one
provider: refused (ToS abuse); multi-provider rotation: fine, per original
design.

## 2026-08-23 (01:10) — END-TO-END PIPELINE VERIFIED WORKING

First complete run in project history: scan (212 companies, 680 jobs) ->
auto-rescore (680/680 scored, merge-fix held, nothing clobbered) -> filtered
feed. Feed shows 1 job (PhonePe SRE Rust, 81%, posted Aug 7) — verified
CORRECT, not a bug: traced all 680 through every pipeline stage; the other
candidates fail 30-day recency (old postings) or seniority/location/score
filters honestly. Score distribution sane (median ~35, relevant SWE jobs
56-81). PENDING bug fix confirmed working with real data: 0 pending stuck,
0 PENDING in feed.
User verification of feed in morning + decide merge to master. Product
knobs to discuss: 30-day recency window, min_match_score 55, company list
composition (many non-tech-heavy companies), 3 new boards activate on
restart.

## 2026-08-23 (01:25) — Fresher-only filters set; session close

User has 0 yrs experience: ceiling set to 0, internship/trainee roles added
to targets, seniority exclusions extended. Verified live (ceiling parses 0,
endpoint enforces). Store audit: ZERO software internships among 680 jobs -
company list produces senior-heavy roles. Internshala probed: HTTP 200,
150 internship markers in plain HTML, no bot protection - BUILD FIRST NEXT
SESSION. Then: Adzuna/YC/Instahyre/Wellfound probes, Apify+friend-key pool
(multi-provider rotation OK, self-created duplicate accounts refused),
LinkedIn awaiting explicit daytime confirmation, UI direction awaiting
user's list, merge-to-master sign-off pending user trust in feed.

## MASTER BACKLOG (keep this section updated; user will ask "what's left")

Sources & keys:
[ ] Internshala fetcher (FIRST - fresher/internship core need, probed open)
[ ] Wire user's 12 Gemini + 1 Groq keys into LLMRouter multi-key rotation
    (VERIFY LLMRouter actually supports key pools; SAFETY: keys must go in a
    gitignored file, NOT tracked config.json - it goes to public GitHub)
[ ] Apify token(s) from user + friends -> Naukri/bot-protected companies
[ ] Adzuna free key (user signup) + fetcher
[ ] Probe & build: YC Work at a Startup, Instahyre, Wellfound
[ ] LinkedIn via Apify public listings - ONLY after explicit daytime user
    confirmation + product-context.md boundary rewrite
[ ] Expand company list toward fresher-hiring companies (target 1000;
    seed from Cutshort/Internshala company names)

Engine:
[ ] Audit threshold auto-adjustment (threshold_optimizer, trial_periods)
    once real usage data exists
[ ] Audit feedback learning loop + auto-revert (needs user ratings first)
[ ] Audit Insights dashboard data accuracy
[ ] Wire vision_fallback_parser into live scan loop (known unbuilt)
[ ] Scan-vs-rescore write race: deeper fix (scanner holds stale store copy
    for its whole run; merge-fix on rescorer side only)
[ ] sentence-transformers/feedparser Section 5 notes are Windows-laptop
    stale; both work on this Mac - update product-context.md Section 5

Product/UX:
[ ] UI: collect user's complaint list -> targeted fixes vs redesign (8B:
    present 2-3 directions, one page at a time)
[ ] Rescoring everything through LLM tier once keys land; THEN recalibrate
    min_match_score (current 55 calibrated on local-tier scores only)
[ ] Merge fix/pending-scoring-bug -> master after user sign-off (includes
    re-audit stance toward debug-branch changes; 2 regressions found so far)
[x] DONE 2026-08-23 02:35: Parallelize ScanCoordinator.run_scan - two-lane
    (API parallel 12 workers / browser sequential), locks, ATS learning.
    Measured: full cycle 26min vs 55min; dormancy cuts more after cycle 3.
[x] DONE 2026-08-23 02:35: Active/dormant tiering (scan_scheduler.py),
    9/9 tests, live streak tracking on 210 companies.
[ ] Remove junk test companies from companies.json (BulkTestCorp1-5 etc -
    old AI session pollution; audit full 212 list)
[ ] 46 jobs in store unscored after cycle (added mid-scoring-pass edge);
    they score next cycle - consider follow-up if it grows
[ ] Oracle Cloud deployment (moved up from far-future - user committed):
    Always Free A1 (4 OCPU/24GB); expect more bot-blocks from datacenter
    IP -> Apify becomes more important; keys via env vars on the VM.

## 2026-08-23 (03:35) — UI lane A done (objective feedback fixes)

Upload error surfacing (was silent!), toast error variant, transparent
empty-feed breakdown (pipeline filter_breakdown -> /api/jobs -> UI).
Verified in real headless browser: renders '737 collected... 616 role
mismatch' empty state on own page load. UI lanes B (visual tweaks) / C
(redesign) still awaiting user's list/decision.
Insight from breakdown: role filter is the feed's bottleneck (616/737) -
worth revisiting target_role list with user (many are analyst/ops roles
that would never pass anyway, but 616 suggests titles like 'SDE'/'Member
of Technical Staff' may be slipping through the cracks - review matching).

## 2026-08-23 (03:45) — First applicable job in feed; store races fixed for real

Chain of fixes: (1) target_role expanded (SDE/MTS/SRE/Developer synonyms) -
role drops 616->594; (2) board jobs were never persisted (search cycle
saved only curated survivors) - now store-first, filter-at-read; (3) the
REAL store race: ScanCoordinator's snapshot saves deleted other writers'
jobs AND fresh scores - both save sites now do match-preserving merges.
Verified live with concurrent scan: 777/777 scored, 40/40 board jobs in
store+scored, feed = Tech Intern @ Springer Capital 82% (cutshort!) -
the user's first genuinely applicable job. Scan-race deeper fix: DONE
(remove from backlog). Microsoft titles have location text glued on
(scraper bug, backlog). 46-unscored mystery: same race, resolved.

## 2026-08-23 (afternoon) — Company expansion session 1: 202 -> 260 verified

Method: live ATS API probing (scratch/ats_probe.py - greenhouse/lever/
ashby/smartrecruiters/workday), strict India-eligibility (generic remote
excluded), per-batch test scans through BrowserScanner. 7 batches run.
Honest exclusions: verified-real-but-not-India-hiring (Palantir, Brex,
Affirm, Instacart, Reddit, Canonical, HRT...) and 7 earlier adds culled
on strict re-audit. Junk BulkTestCorp1-10 removed.
LEARNED: (a) Workday GCC tenants unlock the biggest India employers -
pattern tenant/wdN/site, more tenants to hunt: AMD, Expedia, Honeywell,
Caterpillar, Boeing, TI (careers.ti.com is custom-wrapped workday);
(b) Indian startups (Chalo, Jar, MPL, Spinny, GreyOrange...) mostly on
Keka/Darwinbox/custom - need browser-lane verification pass or new
extractors; (c) consulting GDCs (Deloitte USI, EY GDS, PwC SDC, KPMG
GS) use SuccessFactors/Avature/Oracle HCM - NOT scannable by our
extractors yet; adding them now = dead rows. Backlog: SuccessFactors
extractor unlocks that whole category + Siemens/Bosch-class GCCs.
Path to ~1000: more greenhouse/lever batches (~100-200 realistic),
Workday tenant hunt (~50-100), Keka/Darwinbox extractor (~100+ Indian
startups), SuccessFactors extractor (~100+ GCC/GDC). Probe script is
reusable; continue next session.

## 2026-08-23 (16:20) — Company expansion session 2: 260 -> 270

Rounds A+B: 106 more probes, 10 verified adds (4 devtools/infra via
greenhouse, 6 Workday GCCs incl. Medtronic/Stryker/CVS/HP), 2 upgrades
(Meesho, Groww). Hit rate collapsed to ~15% - the public-ATS-API pool
for India-hiring companies is nearly exhausted at ~270.
Remaining path to 1000 (in yield order):
1. Keka/Darwinbox/Zoho Recruit extractors -> unlocks 100+ Indian
   startups already identified as misses (Chalo, MPL, Spinny, Testbook,
   Classplus, OYO, Rebel Foods, PayU...)
2. SuccessFactors extractor -> unlocks consulting GDCs (Deloitte USI,
   EY GDS, PwC SDC, KPMG GS) + Siemens/Bosch/SAP-class GCCs (100+)
3. Workday tenant hunt w/ web search per company (~50+: AMD, Expedia,
   Optum, Lowe's, GE trio, Nike, airlines...)
4. Oracle HCM/Taleo/iCIMS extractors -> banks & legacy enterprises

## 2026-08-23 (16:40) — Internshala fetcher live

40 internships/location, wired as 7th source, verified through full
cycle into store. Store now 9.4k jobs (270-company scan imported global
postings; filters handle at read; scoring incremental).

## 2026-08-23 (17:45) — LLM scoring LIVE (12 Gemini + 1 Groq)

Keys in .env (gitignored). Two-pass scoring: local pass filters 10.2k
jobs, refinement pass LLM-scores all feed candidates (2061), persisting
every 25. Fixed en route: missing google-generativeai dep, stale zero
quota slot, key-killing error handler (now cooldowns), scanner merges
downgrading refined scores. Refinement runs ~3.5h in background at paced
RPM; feed gets honest scores progressively (LLM correctly rejects senior
roles local tier overrated). Groq untested live (gemini never exhausted
during verification) - watch first Groq fallback.

## 2026-08-23 (19:00) — LinkedIn public listings live + Groq fixed

LinkedIn boundary narrowed by user's explicit repeated daylight request
(steering docs updated + committed): public guest endpoint only, no
login ever. Fetcher live as 8th source: 30 jobs/cycle, verified in store.
Groq: SDK was missing AND model retired (llama-3.1-8b-instant ->
openai/gpt-oss-20b). Verified live scoring. Fallback tier now real.
Quota math confirmed for user: refinement = one-time ~2k calls (~172/key
= 11% of daily); steady state = few hundred calls/day pool-wide.

## 2026-08-23 (19:20) — Keka extractor built (Indian startup portals unlocked)

Public API found per tenant: <tenant>.keka.com/careers/api/jobs/default/active
(no key). Extractor + dispatch + parallel-lane registration done. Probed 72
tenants: 6 live-with-jobs added (Teachmint 31, Jupiter 12, Adda247/Niyo 10,
Zluri 7, Chalo 2), 4 empty-but-valid. Hit rate lower than hoped - many
Indian startups use Darwinbox/Zoho Recruit/custom instead. NEXT: same
XHR-observation trick for Darwinbox (darwinbox.in tenants) and Zoho
Recruit, then SuccessFactors for GDCs.

## 2026-08-23 (19:30) — Darwinbox/Zoho Recruit: NOT pattern-discoverable

Attempted same trick as Keka. Both platforms serve a catch-all 200 for ANY
subdomain (verified with nonsense tenant control test), and real portals
render nothing at guessed paths - Darwinbox portals use per-company custom
paths/IDs, Zoho Recruit needs per-tenant portal IDs. No bulk-discoverable
pattern => not worth brute-forcing. Path if wanted later: read each
company's careers link from its own website (needs per-company crawl).
PIVOT: 166 of 275 companies are ats='custom' (slow browser lane, often 0
jobs). Converting those to real ATS endpoints is higher yield than new
platforms - measure which custom companies actually produce jobs first.

## 2026-08-23 (19:40) — Custom-ATS conversion (higher yield than new companies)

126/166 custom companies were scanning to ZERO every cycle. Built an ATS
detector (follow career_url, match platform signatures). 25 detected;
13 converted to supported APIs and verified scanning (Accenture 60,
Freshworks 99, Qualys 59, Sarvam 58, Barclays 55, Vyapar 73...).
KEY UNLOCK: detector found REAL Darwinbox/Zoho tenant hosts that pattern
guessing couldn't (unacademy.darwinbox.in, pwhr.darwinbox.in,
healthify.darwinbox.in, porter.darwinbox.in, leadsquaredhrms.darwinbox.in,
myhr.darwinbox.in(PharmEasy), dbx.darwinbox.in, quadeye.zohorecruit.in,
go-yubi.zohorecruit.in) -> build Darwinbox + Zoho extractors NEXT using
these real tenants, then re-run detector over remaining 141 custom rows.

## 2026-08-23 (22:45) — Darwinbox: dead end for public extraction (verified)

Real tenants (unacademy/pwhr/healthify/porter/myhr .darwinbox.in) load an
Angular shell showing only the company name - NO public job list. API
namespace found (/ms/candidateapi/*: groupcompanycareersetting,
getLandingPage) but returns 403 to direct requests (Cloudflare) and the
portal redirects to /user/login. Conclusion: Darwinbox candidate portals
don't publish jobs anonymously at the careers path; those companies must
be reached via their own website's embedded listings instead. Stopping per
"2 attempts then report" rule. Zoho Recruit deferred (same catch-all
problem, 3 companies only). PIVOT to LLM page-learner: platform-agnostic,
the real answer to "robust for all career pages".

## 2026-08-23 (23:00) — LLM page-learner + jobs-URL discovery built

Learner: compressed DOM -> LLM -> CSS selectors, applied by the existing
parser, validated by store_integrity_checker, persisted to pattern_store
(one LLM call per company, ever). Shares scoring key pool. Refuses to
invent structure (confidence gate).
Verified honest diagnoses on real dead companies: Google India career_url
= nav-only page; Amazon India = login gateway; Flipkart = marketing page;
Smallcase = 404; Zerodha = genuinely no openings; Darwinbox = landing page.
=> ROOT CAUSE of the 119 zero-yield companies is WRONG career_urls, not
weak parsing.
URL discovery: learner returns jobs_url hints and the scanner follows
them. Flipkart -> discovered flipkart.turbohire.co (TurboHire = new
platform). Extraction from JS-heavy hint pages still 0.
NEXT (in order): (1) TurboHire extractor + re-run discovery sweep to
collect more platform destinations; (2) sweep all 119 zero-yield companies
with the learner, persist discovered jobs_urls back into companies.json;
(3) re-verify BrowserStack/Postman/Hasura greenhouse tokens (now 0 jobs);
(4) vision fallback for pages that defeat text parsing.

## 2026-08-23 (23:20) — TurboHire extractor + first discovery sweep

TurboHire extractor built from the learner's Flipkart discovery (token/noauth
+ careerpagev2/filteredjobs): Flipkart 0 -> 8 real jobs.
Discovery sweep (12 of 118 dead companies): 5 real jobs-URLs found; VMware
India converted to Workday (38 jobs), LinkedIn India fixed (15 jobs), 3 URL
improvements pending per-site work (Byju's/Meta/Oracle).
Honest blockers found: TCS + Infosys = WAF/Akamai anonymous block (mark as
unscannable, not parser bugs); Zomato/Netflix = protocol errors.
Rate-limit contention: sweep + background refinement share the key pool ->
429s handled by cooldowns, no keys killed. Run sweeps when refinement idle.
REMAINING: 106 dead companies still to sweep (batches of ~12), then
re-verify BrowserStack/Postman/Hasura greenhouse tokens, then vision
fallback for the JS-heavy stragglers.

## 2026-08-23 (23:25) — Company sourcing strategy: measured comparison

Claude (separate chat) correctly reported it cannot verify companies live and
that name-generation punts all real work downstream. Tested the alternative
sources with real numbers:

1. MINING our own collected postings (live-verified hiring, zero guessing):
   10k jobs -> 102 distinct new company names -> 95 plausible after junk
   filter -> ATS-probed -> only 6 verified, and just 1 with India jobs
   (Jitterbit). Board postings skew to tiny companies with no public ATS
   API. Weak source for our pipeline; keep as passive trickle.
2. MEMORY-SEEDED + LIVE PROBE (what worked today): 68 added from ~440
   probes across categories. Well-known-company pool is thinning (hit rate
   fell 60% -> 15%).
3. NOT YET TRIED: per-category live web search for CURRENT lists (e.g. new
   GCCs opened 2025-2026), then probe each name. Claude's point that 2,100+
   GCCs exist means the addressable pool is far larger than either of us can
   recall - search is the right tool for discovery, probe for verification.
4. APIFY (user now has 10 keys ~= $50/mo free credit): unlocks Naukri +
   TCS/Infosys (WAF-blocked to us) AND feeds far more company names into (1).

STRATEGIC NOTE for future sessions: 1000 companies is a proxy metric. The
user's actual goal is fresher-eligible job volume. Naukri via Apify likely
delivers more relevant jobs than 700 additional company career pages.
Recommend: Apify first, then search-driven discovery batches.

## 2026-08-23 (23:40) — AUTONOMY: company discovery now runs inside the app

User's requirement: "system should be autonomous, I don't want to come back
to Kiro again and again". Moved company-list building out of my session and
into the app:
- company_discovery.py: propose (mined postings + LLM by rotating category)
  -> verify (live ATS probe) -> gate (India jobs AND fresher jobs) -> record
  (company_discovery_log.json, incl. rejection reasons). Caps: 60 candidates,
  25 adds per cycle. A hallucinated name cannot enter - it fails the probe.
- app.py: _company_discovery_loop background thread, starts 15min after boot,
  runs every 6h.
- scan_coordinator.py: per-company fresher-yield tracking
  (fresher_jobs_last_scan/_total, last_fresher_at, fresher_zero_streak) so
  the list self-cleans toward the 70-80% fresher-active goal.
- GET /api/company-health: live fresher_active_pct vs 75% target + recent
  discovery cycles (for the Insights page).
Baseline at build time: 277 companies, 122 producing, 4 fresher-active
(1.5% by the strict rolling definition; 15% by the looser one-off measure).

STILL HUMAN-DEPENDENT (things autonomy cannot cover):
- Apify keys (user has 10, not yet pasted) -> Naukri/TCS/Infosys
- Adzuna key; UI direction; merge-to-master sign-off
- Genuinely new product decisions
NEXT AUTONOMY STEPS: (a) wire fresher_zero_streak into partition_companies
so dormancy demotes non-fresher companies, (b) let discovery also run the
LLM page-learner on candidates whose ATS is unknown, (c) surface
company-health on the Insights page.

## 2026-08-23 (23:50) — HYBRID: worker acts, orchestrator teaches weekly

User directive: autonomous worker is less precise; make it hybrid with a
weekly orchestrator review, and NEVER drop a company that might post a
fresher role tomorrow. Implemented:
- company_watchlist.json: verified-real-but-not-fresher-today companies are
  PARKED and re-probed every cycle (recheck runs first, before new
  discovery). Promotion is automatic the moment a fresher role appears.
- discovery_rules.json: the teaching channel the worker obeys each cycle
  (blocklist, force_watch, min_fresher_to_admit, dated notes).
- WEEKLY_REVIEW.md: auto-generated 7-day rollup incl. a 'near-misses'
  section aimed at the worker's riskiest calls.
- Weekend review ritual written into orchestrator-mode.md steering.
Verified over 3 cycles: 2 companies parked not dropped, recheck counters
incrementing, review file correct.
REMINDER FOR NEXT SESSION: user's stated main motive is job opportunities
ASAP -> Apify keys (10, still unpasted) unlocking Naukri/TCS/Infosys remain
the highest-value item, above further company-list growth.

## 2026-08-24 (00:20) — Apify keys: all 10 INVALID (verified)

User supplied 10 apify_api_* tokens. All 10 rejected by Apify with
"user-or-token-not-found" / 401. Verified it is not our request format:
tested Bearer-header auth AND ?token= query auth (both 401), plus a
no-token control (401 as expected). Token length 46 matches the real
format, so they look right but do not exist on Apify's side - i.e. fake,
revoked, or from accounts that no longer have them.
Removed from .env so the app does not burn cycles on dead keys.
BLOCKED until real tokens arrive: Naukri, TCS, Infosys (WAF-blocked to us),
Glassdoor. Each friend must copy their OWN token from
apify.com -> Settings -> API & Integrations -> Personal API token.

## 2026-08-24 (00:45) — Closed both open tasks (LinkedIn targets + dead boards)

Called out by user for task-switching without finishing. Both now closed:
A) LinkedIn fresher-recruiter target queries (Apify workaround, since the
   user's Apify account is DISABLED - likely multi-account anti-abuse).
   12 rotating queries reach TCS/Infosys/Wipro/Cognizant/Capgemini etc.
   Verified: 57 LinkedIn jobs/cycle, 19 new persisted, 101 in store.
B) Dead greenhouse boards: BrowserStack migrated to Workday (0->51 jobs);
   Postman's URL had been clobbered by my own earlier URL-discovery sweep
   (marketing page overwrote the board URL) -> restored, 113 jobs; Hasura
   is no longer on greenhouse and now correctly falls through to the
   learner (429 that attempt - retries on next scan).
C) Bonus fix found via Hasura: stale-ATS fallthrough. Declared-ATS failure
   no longer aborts a scan; heuristics + learner now run.
LESSON RECORDED: URL discovery must never overwrite a working ATS board URL
with a non-ATS page - add a guard if that sweep is automated later.

## 2026-08-24 (01:00) — LinkedIn broadened to full fresher sweep

222-query space (30 fresher roles x 7 India locations + 12 mass-recruiter
queries), 6 rotated per cycle @20 results => full sweep in ~1.5 days.
Verified: 94 jobs in one slice (46 fresher-titled), full cycle persisted 81
new; store now 193 LinkedIn jobs / 10.9k total.
NOTE: LinkedIn is now the highest-yield FRESHER source we have (career pages
are senior-heavy). If LinkedIn ever starts 429ing, reduce queries_per_cycle
before anything else.

## 2026-08-24 (01:20) — Indian board probe results + Shine live

Naukri/Indeed/Glassdoor: PARKED (not dropped) - all need a third-party
scraper; user's Apify account is disabled. Glassdoor recommended for
permanent deprioritisation (heaviest protection, listings duplicate career
pages). LinkedIn's fresher sweep now covers much of their value.
Probed 5 untested Indian boards: Shine OPEN (built, 9th source, highest
fresher density seen: 33/40 titles), Freshersworld partially reachable
(queued), TimesJobs needs browser (queued), Foundit 403, Instahyre 403.
Free aggregator substitutes still worth user signup: Adzuna, Jooble.

## 2026-08-24 (01:45) — Freshersworld live (10th source)

Fresher-native Indian board, plain HTML, no key. 22 of 27 sampled jobs are
explicitly "0 Years" experience - the highest 0-exp density of any source.
Fixed location junk ("Less M") and anonymous-agency company labels.
Per-path yields measured; city paths thin (1-3) but kept.
Source lineup now: 277 career pages + internshala + shine + freshersworld +
linkedin(222-query sweep) + cutshort + remoteok + remotive (+ indeed/naukri
stubs parked pending Apify).
NEXT in my queue: TimesJobs (needs browser), resume discovery sweep on ~100
dead companies, threshold recalibration after LLM refinement, wire
company-health into Insights UI.

## 2026-08-24 (02:00) — TimesJobs: NOT viable (2 attempts, stopped)

Next.js app renders only filters/chrome - no listings after 12s + scroll in
a real browser; networkidle never settles (45s timeout). No job API observed.
Verdict: needs either a logged-in session or a third-party scraper. Parked
alongside Naukri/Indeed/Foundit/Instahyre.

## 2026-08-24 (02:10) — Sweep batch 2 + RPM-aware quota guard

Sweep (14 companies): Google India + IBM India URLs corrected to real job
search pages (need learner on next scan). Hard blocks re-confirmed with
evidence: TCS/Infosys Akamai, Uber Cloudflare Turnstile, Zomato/Netflix/
Morgan Stanley protocol errors -> these are NOT parser problems; only a
third-party scraper or a different route will reach them.
INSIGHT: 429s were per-minute rate limits, not daily quota (265/18000 used).
has_headroom now requires a majority of keys free of cooldowns, so
background work (discovery, page learning) yields during RPM contention.
Verified by simulation (7/12 cooling -> refuse, 3/12 -> allow).
Queue remaining: threshold recalibration after refinement completes, wire
company-health into Insights UI, more sweep batches (~90 dead left),
Adzuna/Jooble fetchers when user supplies keys.

## 2026-08-24 (02:45) — Oracle HCM extractor + Insights card + sweep 3

- Insights "Company List Health" card live (fresher-active % vs 75% target,
  producing/watchlist counts, last 5 discovery cycles with skip reasons).
  Browser-verified; showed a real autonomous cycle that added Jitterbit.
- Oracle Cloud HCM extractor built (JPMorgan 0 -> 46 jobs, 32 India). KEY:
  expand=requisitionList.secondaryLocations is mandatory or the API returns
  an empty list with HTTP 200. Generalises to other banks/enterprises on
  Oracle Fusion - worth probing more tenants.
- Adzuna + Jooble keys stored in .env (Adzuna validated live). Fetchers NOT
  built yet (user asked to finish queue first). Jooble note: 500-request
  default limit -> design conservatively (few calls/cycle).
- LESSON: LLM-supplied URLs can be truncated mid-string (/sites/CX_) -
  validate before storing.
Remaining queue: Adzuna+Jooble fetchers, threshold recalibration, more sweep
batches (~85 dead), probe more Oracle HCM tenants.

## 2026-08-24 (02:55) — Adzuna + Jooble live (12 sources total)

Adzuna: 39/40 sampled jobs fresher-titled, 60/cycle, 3 rotating queries.
Jooble: STRICT budget - free key documented at 500 requests TOTAL, so 1 call
per cycle + lifetime counter (jooble_usage.json) hard-stopping at 400.
Verified 20 jobs/call; some query slices legitimately return 0.
Source lineup (12): 275 career pages, internshala, shine, freshersworld,
linkedin (222-query sweep), cutshort, remoteok, remotive, adzuna, jooble
(+ indeed/naukri stubs parked pending Apify).
Store: 11.6k jobs.
Remaining queue: threshold recalibration (refinement still running), more
sweep batches (~85 dead), probe more Oracle HCM tenants (JPMorgan pattern).

## 2026-08-24 (03:05) — Detector re-run; tenant guessing definitively ruled out

Probed 34 bank/insurer Oracle-HCM tenant guesses x2 sites -> 0 hits.
Combined with earlier Workday/Darwinbox/Zoho results: enterprise ATS tenant
hostnames are NOT guessable. Only the detector (follow the company's own
career URL, match platform signatures) works. Detector re-run over 149
custom companies found 3 (JPMorgan oraclehcm already done, Ola + Ola
Electric turbohire).
Ola converted; its TurboHire portal legitimately has 0 open jobs today.
RECURRING TRAP (twice now): truncated URLs - the LLM hint truncated
/sites/CX_1 and my detector truncated a UUID at 120 chars. Always extract
full identifiers from the live page before storing.

## 2026-08-28 (19:15) — WEEKLY REVIEW #1 (5-day gap; app ran until Aug 24 21:31)

State on return: data intact (12,067 jobs, 276 companies, watchlist 5),
git safety PASS, app was stopped (laptop off ~4 days), 2 stale temp files
cleaned.
Review found TWO REAL DEFECTS - both fixed and verified:
1. Gate counted India-jobs and fresher-jobs independently => Jitterbit
   admitted with 14 senior India jobs + 2 Brazil interns = 0 applicable
   roles. Now requires fresher AND India in the SAME job
   (fresher_india_jobs). Jitterbit now rejected; Hevo/CloudSEK still pass.
2. ScanCoordinator overwrote companies.json from a stale snapshot, deleting
   worker-added rows (Jitterbit "added" 3x, 1 row survived). Both writes now
   merge-safe (_merge_save_companies), proven by simulation.
Worker judgement otherwise sound: all 6 parked/added calls verified correct
live. Taught: force_watch Kellton/Iris/InfoBeans; note that 207/227
rejections were "no public ATS endpoint" -> steer LLM proposals toward
categories that use public ATS platforms.
NEXT: threshold recalibration (refinement state to re-check), ~85 dead
company sweeps, list growth with the corrected gate, merge sign-off pending.

## 2026-08-28 (20:05) — App restarted; feed is alive; refinement resumed

FEED NOW HAS 96 JOBS (was 1 last session). Top: Springer Capital Tech Intern
82%, Deel Backend Node/AWS 80%, Vercel Backend 79%, Pure Storage 79%,
Point72 78%, HP India Full Stack 78%, Vyapar Data Analyst FRESHER 75%,
Signzy SDE-1 Fullstack 73%. Sources visibly mixed (cutshort, linkedin,
career pages) - the multi-source work is paying off.
Store 13.3k -> 14.1k jobs after restart scans. Filter breakdown shows the
role filter is still the big gate (9,004 dropped) then staleness (1,886) and
location (1,346).
Refinement had been interrupted by the laptop shutdown (only 72 jobs had
tier-1 LLM scores, status stuck at in_progress). Re-triggered: cheap pass
re-scored 14,108, then LLM refinement started on 3,110 feed candidates
(~40-60 min at paced RPM). Gemini usage 304 calls today - still ~1.7% of
daily pool.
company-health: 276 companies, 141 producing, 51 fresher-active (18.5%),
watchlist 5.
NEXT: after refinement completes, recalibrate min_match_score against the
LLM-tier distribution (the current 55 was tuned on local-tier scores);
then ~85 dead-company sweeps and list growth with the corrected gate.

## 2026-08-29 (01:45) — LLM refinement was a no-op; scan crash fixed

### Why refinement produced no LLM scores (measured, not inferred)
Tested every key individually: **9 of 10 Gemini keys returned 429 after only
23-52 calls**, so the free per-key daily cap for gemini-3.5-flash is ~50 —
not the 1500 config assumed. Groq rate-limits per ORGANISATION on tokens, so
its nominal 14,400 requests/day is unreachable. Real ceiling is a few hundred
LLM scores per day, total.

Three defects compounded it:
1. **No daily rollover.** used_today only incremented, quota_remaining only
   decremented. A key that hit its cap stayed dead forever even though
   providers refill nightly — the pool shrank to nothing permanently.
2. **Fictional quotas** meant the router kept selecting exhausted keys.
3. **Hardcoded gemini-first order** — every job burned 10 failed API calls
   before reaching Groq, then fell back to local scoring.
Plus 4 keys still stuck at quota_remaining=0 from the older kill-on-any-error
bug. Repaired.

Refinement now takes the **top 150 candidates by local score** (REFINE_CAP)
instead of all ~3000: at a few hundred calls/day, refining everything takes a
week and starves the top of the feed, which is the only part read.

### Scan crash: "cannot switch to a different thread (which happens to have exited)"
Killed all browser-lane companies and then the whole cycle. ScanCoordinator
reuses one BrowserScanner while each cycle can run on a new thread; Playwright
binds a browser to its creating thread. Once a cycle ended without close(),
the cached browser belonged to a dead thread, and close() raised the same
error from inside finally — permanently poisoning the scanner.

**Trap worth remembering: CPython REUSES thread idents.** A reproduction showed
the new scan thread receiving the dead thread's exact ident, so a
threading.get_ident() guard passes while the browser is actually orphaned.
Compare Thread OBJECTS instead.

Follow-on found in the logs: parallel API-lane workers WERE driving Playwright
(a failed API extraction falls through to the page scan), 12 workers fighting
over one browser. Workers now run with allow_browser=False and return
"deferred_needs_browser"; the coordinator re-scans those sequentially on the
browser-owning thread. Chosen over per-worker browsers, which would mean up
to 12 Chromium instances on a laptop.

### Verified this session
- Live scan: 0 thread errors, 0 worker relaunches, 19 deferrals, 126+ scanned
- Forced paid-tier score returns **tier 1 via groq**
- Refinement: "top 150 of 3003" -> 150/150 processed; tier-1 272 -> 314
- Feed **96 -> 158 jobs**; top two are LLM-scored: Arohana Python Full Stack
  Intern/Entry 90%, Springer Capital Tech Intern 85%
- 16 existing tests pass

### Known limits
Only ~28% of the 150 refinement attempts got an LLM score; the rest hit Groq's
per-minute ceiling. ~500 more Gemini calls unlock when quota resets (~12:30 PM
IST). Silent fallback is now loud (needs a restart to take effect).

## 2026-08-29 (04:20) — SuccessFactors unlocked; discovery batch run

- **New ATS: SuccessFactors** (extractor + discovery probe). EY GDS live in
  the store with 200 India jobs (51 fresher-titled: Industrial Trainee /
  Analyst / Associate / Staff); SAP India with 50. Both scan on the parallel
  API lane. Probed and ruled out: Deloitte USI (Avature), Cognizant (403),
  Bosch, Siemens, Wipro, HCLTech, TechM, LTIM, Genpact, DXC, Mphasis.
- **Watchlist re-probe (all 5):** every company correctly stays parked.
  Kellton still 2 fresher roles outside India; Iris 4 India roles none fresher.
- **Discovery batch (34 names):** Tower Research Capital ADMITTED (16 India,
  1 fresher-India, greenhouse) -> 279 companies. WorldQuant, Dhan, Refyne,
  GoKwik (19 India roles, keka), Skyflow parked with India presence ->
  watchlist 12. GoKwik is the likeliest near-term promotion.
- Store: 15,281 jobs.

## 2026-08-29 (16:15) — Queue cleared: recalibration, learner fix, growth

- **Quota rollover corrected to Google's clock.** Refill is midnight
  US-Pacific = 12:30 PM IST; rolling on the IST date left fresh quota
  invisible for hours (observed live). Verified: a key dead at 429 last
  night scores again.
- **Refinement pass completed on fresh quota: 150/150, tier-1/2 scores
  314 -> 464.** Only 92/500 Gemini calls used.
- **Feed threshold recalibrated with real data** (n=464 LLM vs n=2896
  local): local scores compress into 50-69 (median 57, inflated); LLM
  scores spread 0-98 (median 35) and demote 43% of local-approved jobs
  below 30. Gate is now tier-aware: LLM-verified jobs get a 10-point lower
  bar. 20 honest LLM 45-54% jobs entered the feed.
- **Page learner now rotates up to 3 keys** instead of giving up on the
  first 429. A standalone zero-yield sweep script was built, then
  deliberately discarded: dormancy tiering already retries zero-yield
  companies with the learner every 4th cycle, and a second process would
  race pattern_store.json.
- **Growth:** LambdaTest ADMITTED (10 India, 1 fresher-India, keka) -> 280
  companies. NK Securities (16 India), Testsigma, DevRev (23 India), ZF
  Group (successfactors) parked -> watchlist 16. Learned: verify_candidate
  rejects names whose slug is <3 chars (ZF) before probing - probe manually
  for short names.
- **All 28 tests pass.** Feed 158, LLM-verified 30, 0 scan errors.
- Monitor: one "Unterminated string" store read hit a TEST process; store
  parses fine, app log clean, production writers verified atomic
  (tmp+fsync+replace). Not reproduced.
