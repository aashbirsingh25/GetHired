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
