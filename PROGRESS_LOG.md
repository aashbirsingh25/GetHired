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
