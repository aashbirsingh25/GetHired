GetHired — Project Memory & Working Rules for Claude Code

Read this file in full before doing anything. This is your persistent memory —
this project has already burned significant time/quota on agents (ChatGPT,
Antigravity) working too broadly without verification. Do not repeat that.

Repo: https://github.com/aashbirsingh25/GetHired
Owner/only-authorized-remote: aashbirsingh25 (this repo only — see Section 0)

---

## 0. FIRST ACTIONS — DO THESE BEFORE TOUCHING ANY CODE

This machine may have other accounts/repos configured on it (this laptop is
borrowed). Before any commit:

```bash
cd <path-to-GetHired-repo>
git config user.name
git config user.email
git remote -v
```

Report these three outputs to me before proceeding. Confirm:
- `git remote -v` points to `https://github.com/aashbirsingh25/GetHired.git` —
  if it points anywhere else, STOP and ask.
- Set LOCAL (not global) git identity for this repo only if needed:
  `git config user.name "..."` / `git config user.email "..."` (no `--global`).
- Never touch, commit to, or push to any repo other than
  `aashbirsingh25/GetHired`. Never use or reference any other account's
  credentials, even if they're conveniently already configured on this machine.

---

## 1. ORIGINAL PRODUCT VISION (what this is actually supposed to be)

GetHired is a personal, self-hosted, zero-recurring-cost job discovery and
ranking engine for a fresher/entry-level (0–2 yrs) software engineering
candidate, focused on India (Gurugram/Bangalore/Delhi + remote).

The core loop:
1. Given a list of companies + job boards, autonomously visit/search them in
   the background (2–3x/day), not on-demand only.
2. Extract job postings — via direct scraping, ATS APIs, or job-board
   scrapers (Apify etc).
3. **Original scanning philosophy** (important — the current implementation
   only partially realizes this): open a career page, understand its
   structure, apply filters, extract jobs, and *learn/remember the site's
   structure* so future scans are faster. If deterministic parsing fails,
   fall back to an LLM to understand the page, and persist what it learned
   for reuse. The current codebase implements this well for known ATS
   platforms (Greenhouse/Lever/Ashby/Workday/SmartRecruiters — direct API
   extraction, very reliable) but the *general* "learn any arbitrary site via
   LLM/vision and remember it" version is only partially built
   (`vision_fallback_parser.py` exists but is NOT wired into the live scan
   loop — see Section 5). Don't assume this is finished.
4. Score every job against the user's resume using semantic matching (not
   keyword matching — "Software Developer" must match "Software Engineer"),
   via a tiered fallback: LLM APIs → local Ollama → local
   embeddings/keyword fallback. Show a transparent match score + explanation
   ("why this matched, what's missing"), never a black box.
5. Learn from user feedback (thumbs up/down per job) to improve future
   scoring and auto-adjust filter thresholds over time — with self-testing/
   auto-revert if an adjustment measurably makes things worse.
6. Full transparency everywhere: quota usage, threshold changes, routing
   decisions, confidence scores — all visible to the user, never hidden.
7. Zero recurring cost: free-tier APIs only, multiple keys rotated by quota,
   self-hosted (Oracle Cloud free tier), Ollama as a genuinely free local
   LLM fallback so the app never dies when API quota runs out.

**Frontend** (single-page, 5 sections): Profile/resume upload → Search &
Criteria (simple filters + opt-in advanced priority-tiered filters +
background auto-search config) → Jobs Feed (job cards with expandable
score breakdown + explanation) → Application Tracker (Viewed/Saved/Apply
Later/Applied, with Applied split into Applied/Referral/Shortlisted) →
Insights (transparency dashboard: quota %, score trends, self-improvement
log, recommendations) → Settings (company/job-board list management).

**Hard boundaries — do not revisit these without the user explicitly asking:**
- **No fabricated/mock/synthetic data may ever touch production files**
  (`jobs_store.json` etc). A synthetic job generator
  (`_generate_career_jobs`) previously did this silently and caused a
  serious bug (fake jobs with a hardcoded wrong fallback URL). It is
  disabled. **Never re-enable it**, even if it looks like a convenient
  fallback when a scan returns few results.

---

## 2. CURRENT GIT STATE (verified directly from GitHub, not just chat memory)

master (3 commits, clean, last: Aug 14)
b316c6f Initial commit
a99b3ad Update gitignore for runtime data
390c64e Add project documentation ← current tip of master

origin/debug/gethired-stability (branches off 390c64e, +17 commits, Aug 15-16)
af5792f .. 819464c — "Phase 1" through "Phase 16" hardening + 2 follow-ups
Fully committed and pushed. HEAD = 819464c. Working tree was reported clean.
This is ChatGPT's hardening work.

origin/coderabbit-review (branches off 390c64e, +2 commits)
Adds only .coderabbit.yaml (review-bot config). Trivial, zero-conflict-risk
to merge anywhere. Not yet merged into master or debug branch.

Neither debug/gethired-stability nor coderabbit-review has been merged into
master. Master is the last known-good, pre-ChatGPT baseline.


I (Claude, in a prior chat session) reviewed the full diff of
`debug/gethired-stability` against `master` directly on GitHub. Verdict:

**Genuinely good, low-risk improvements** (safe to keep):
- `job_deduplicator.py` — smarter title matching (Jaccard similarity +
  seniority-keyword check), fixes false-merging "Engineer" with "Senior
  Engineer."
- `local_scorer.py` — expanded skill regex (Go/C/R with word boundaries),
  skill synonym canonicalization (Postgres→PostgreSQL, JS→JavaScript),
  two-tier seniority cap (60% for Senior/Lead/Manager, 35% for
  Director/VP/CTO/Principal/Staff), role-category caps for non-technical
  roles.
- `store_integrity_checker.py` — extended non-job blacklist.
- `background_search_worker.py` — atomic file writes (write to temp file +
  `os.replace()`, prevents corruption on crash mid-write). Genuinely good
  practice, keep it.
- `pipeline.py` — role/location matching improved (word-boundary regex,
  location synonyms: Gurugram↔Gurgaon, Bangalore↔Bengaluru, Delhi NCR
  expansion).
- `browser_scanner.py` — ATS board-token auto-discovery (finds the *actual*
  Greenhouse/Lever board token when it doesn't match the company name
  obviously — e.g. Razorpay's actual token wasn't "razorpay"), fixed a real
  Python bug where `if response:` on a `requests.Response` silently
  evaluates `False` for any non-2xx status (breaking 404-handling branches
  in every ATS extractor), Google-search-URL fallback removed from
  `apply_direct()` in `app.py` (was producing garbage "apply" links).
- Fake-job-generator and LinkedIn boundaries: **confirmed untouched**, still
  correctly disabled/absent in both branches.

**🔴 THE ONE CONFIRMED REAL BUG — top priority fix (Section 3).**

---

## 3. THE CONFIRMED BUG — FIX THIS FIRST, NOTHING ELSE, BEFORE ANY MERGE

In `pipeline.py`, `execute_authoritative_pipeline()` used to synchronously
call `HybridJobScorer` to compute a real match score for any job missing a
cached score, or whose cached score was based on a stale/old resume version
(via a `resume_version_hash` check). On `debug/gethired-stability`, that was
replaced with code that just stamps:

```python
{"score": 0, "match_grade": "PENDING", "confidence": "pending", ...}
```

with a comment implying a "background rescorer" would pick these up later.
**That background rescorer does not exist anywhere in the codebase.** I
searched for it directly; it was never built. On top of that, there's a
special case in the `min_match_score` filter that lets PENDING jobs through
regardless of score — so instead of being hidden, these permanently-unscored
0%-match jobs flood the feed.

**Net effect:** any job without an existing cached match — including every
single job any time the user updates their resume — shows up permanently as
0%/PENDING in the feed instead of getting a real score. This breaks the core
purpose of the app (ranking jobs by fit).

**Your task, scoped narrowly:**
1. Confirm this bug still exists as described (check `pipeline.py` on
   whichever branch you're working from).
2. Fix it — either (preferred if it doesn't hurt page-load time) restore
   synchronous `HybridJobScorer` scoring for jobs missing a valid/current
   match, exactly like it worked pre-ChatGPT, keeping the newer
   role/location matching improvements from the debug branch; or build the
   actually-missing background rescorer (a lightweight thread/queue that
   scores PENDING jobs within seconds of creation) — your call on which is
   cleaner, but PENDING jobs must never bypass `min_match_score`.
3. Do this on an isolated branch. Do not merge to `master` yet.
4. Ask the user to manually verify live (upload/change a resume, confirm
   jobs actually get real scores, not permanent PENDING) before considering
   this closed. See Section 6 — do not trust automated "PASS" alone.

---

## 4. FILES THAT ARE OFF-LIMITS UNLESS THE USER EXPLICITLY ASKS

- `store_integrity_checker.py`, `job_deduplicator.py`, `local_scorer.py`,
  the atomic-write logic in `background_search_worker.py` — already
  reviewed, working as intended, do not "improve" these as a side effect of
  another task.
- Anything related to `_generate_career_jobs` — must stay disabled, always.
- LinkedIn: RULE NARROWED 2026-08-23 by explicit user decision (asked twice
  across sessions, confirmed in daylight). Allowed: fetching PUBLIC job
  listings via LinkedIn's guest (logged-out) endpoints or third-party
  scrapers (e.g. Apify) — no login, no credentials, read-only. Still
  forbidden forever: using the user's LinkedIn account/credentials for
  anything automated, auto-applying, messaging, or connection automation.
- `.coderabbit.yaml` / CodeRabbit config — fine to merge (trivial), not fine
  to reconfigure without being asked.

---

## 5. KNOWN INCOMPLETE / HALF-BUILT WORK (don't assume these are done)

- **Background rescorer** — doesn't exist (this is Section 3's bug).
- **`vision_fallback_parser.py`** — exists, has a verify script, but is NOT
  wired into the live `browser_scanner.py` scan loop. The vision-based last
  resort fallback (screenshot + vision model) described in the original
  architecture doc is not actually reachable in production.
- **General "LLM learns arbitrary site structure and remembers it"** — the
  original vision (Section 1, item 3) is only realized for known ATS
  platforms via direct API extraction, not as a general learned-structure
  agent for arbitrary career sites. `pattern_store.json` does cache learned
  CSS selectors per company, which is a partial version of this — confirm
  scope before assuming more exists.
- **Urban Company (and similar custom-portal SPA sites)** — returns 0 jobs;
  standard ATS extraction logic doesn't cover fully custom career portals.
  Not yet solved.
- **`sentence-transformers` not loading** — confirmed `pip install`
  succeeded but the app logs `No module named 'sentence_transformers'` and
  silently falls back to a weaker embedding path (or, worse, a SHA-256-hash
  "mock embedding" fallback if both Sentence Transformers AND Gemini
  embeddings are unavailable — that mock path produces near-meaningless
  semantic similarity and should be flagged loudly if it's ever the active
  path, not silently accepted). Likely a venv/interpreter mismatch.
- **`feedparser` not installed** — Naukri RSS fetcher silently contributes 0
  jobs every cycle.
- **Indeed fetcher unconfigured** — no `publisher_id` set, skipped
  gracefully, contributes 0 jobs.
- **Company scan prioritization is naive** — every company scanned every
  cycle regardless of historical yield; dormancy/tiering was deliberately
  deferred, not built.
- **Never actually deployed** — has only run locally via `py app.py` on
  Windows. Oracle Cloud Always Free Tier VM walkthrough was written but
  never executed.
- **Root directory has untracked scratch/debug scripts** (`debug_3_companies.py`,
  `inspect_35_fast.py`, `test_http2_*.py`, etc.) — clutter, not functional
  code. Worth cleaning up or deleting once things are stable, not urgent.

---

## 6. HOW TO WORK ON THIS PROJECT — PROCESS RULES (non-negotiable)

These rules exist because this exact project already got messed up twice by
agents (ChatGPT, and before that an earlier over-broad session) not
following them. Do not repeat that pattern.

1. **One small, scoped task at a time.** No repo-wide audits, no unrelated
   refactors, no drive-by "improvements" to files not in scope for the
   current task.
2. **Never trust automated test "PASS" as proof something works.** This
   project had "63/63 tests passing" while the live app was still visibly
   broken in ways the tests didn't catch. After any fix, explicitly ask the
   user to manually check the real running app before considering the task
   closed.
3. **Always confirm the Flask server was actually restarted** after any
   change to data-loading or scoring logic — a stale running process can
   make a real fix look like it didn't work, or a real bug look fixed.
4. **No new "Phase N" naming or sprawling verify_phaseN.py-style test files.**
   That pattern is exactly what produced the confusing, hard-to-audit
   16-phase debug branch. Scope test additions tightly to what you actually
   changed.
5. **Before committing:** show the user `git diff --stat` and a plain-English
   summary of exactly what changed and why, before pushing anything.
6. **Ask before merging into `master`.** Work on a feature/fix branch, get
   user confirmation the fix works live, then ask before merging.
7. **If a task starts requiring you to touch files outside its stated scope
   to succeed, stop and explain why, rather than expanding scope silently.**

---

## 7. THE AUTONOMOUS WORK LOOP — HOW TO OPERATE WITHOUT BEING PROMPTED

The user will not be feeding you task-by-task prompts. You are expected to
pick the next item yourself, complete it fully (including verification),
document it, and move to the next — looping through Section 9's roadmap
until it's exhausted or you hit a genuine STOP condition (Section 8).

For **every single task**, run this exact loop, in order, no skipping steps:

**Step 1 — State the plan before touching code.**
Write 2-5 sentences: what you're about to do, which files you expect to
touch, and how you will know it worked. If you can't state how you'll verify
it, you don't understand the task yet — go re-read the relevant code first.

**Step 2 — Implement the smallest version that solves it.**
Touch only the files the plan named. If you discover mid-task that you need
to touch something outside that list, stop, explain why, and only proceed
if it's clearly required (not just convenient).

**Step 3 — Verify it yourself, for real, before telling the user it works.**
"Verify" means actually running the thing, not reading the code and
reasoning it should work. Concretely:
  - If it's backend logic: write or run a targeted test that exercises the
    exact change, AND actually start the Flask server and hit the real
    endpoint/flow with a real request — not a mock. Read the actual output.
  - If it's the scanner/scoring pipeline: run it against at least one real
    company/job, not synthetic data, and read the actual resulting JSON.
  - If it's the UI: actually load the page (screenshot it if you have that
    capability) and confirm it renders and behaves as intended — don't just
    confirm the HTML/CSS "looks right" by reading it.
  - If verification fails: fix it and re-verify. Do not report a task as
    done based on a failed or partial verification "probably being close
    enough."

**Step 4 — Write down what you actually observed, not what you expected.**
State the literal output/result you saw (e.g. "ran `curl localhost:5000/api/jobs`
after resume upload, 3/3 test jobs returned real scores, none PENDING" —
not "scoring should now work correctly"). If your only evidence is
"the code should do X," say so explicitly and flag it as unverified, don't
present it as confirmed.

**Step 5 — Commit with an honest message, on a task branch.**
Small commit, message describes what actually changed and why. Do not merge
to `master` without explicit user sign-off (Section 6, rule 6) — but you do
NOT need to ask the user for the next task; move on to it yourself once
this one is genuinely verified.

**Step 6 — Log it, then pick the next task.**
Append a short entry to `PROGRESS_LOG.md` (create it if it doesn't exist) —
one entry per task: date, what was done, what you verified and how, branch
name, status (done / needs user review / blocked). This is your own memory
across sessions — read it at the start of every session before picking a
task, so you never redo work or lose track of what's already fixed.

---

## 8. WHEN TO STOP AND ASK vs. WHEN TO JUST PROCEED

**Proceed without asking** when:
- The task is already scoped in this file (Section 3, Section 9's roadmap)
  and doesn't touch an off-limits file (Section 4).
- You hit an ambiguous small implementation detail with a reasonable
  default (e.g. exact variable naming, minor UI spacing) — just pick
  something sensible and note the assumption in your commit message.
- A test fails and the fix is clearly within the scope of the current task.

**Stop and explicitly ask the user** when:
- The task would require touching an off-limits file (Section 4).
- You're about to merge anything into `master`.
- Verification fails repeatedly (2+ attempts) and you can't identify the
  root cause — don't guess-and-check indefinitely; report what you tried
  and what you observed, and ask.
- You find something that contradicts this file (e.g. a "safe" file from
  Section 2 turns out to have a real problem) — flag it, don't silently
  work around it or silently fix it beyond the current task's scope.
- The task involves a genuinely new product decision not covered by
  Section 1's vision or the user's roadmap (e.g. "should this be a paid
  feature," "should we add a new data source") — these are product calls,
  not engineering calls, and aren't yours to make alone.
- You've completed everything currently in Section 9's roadmap and have
  nothing left queued — don't invent new scope on your own; summarize
  what's done and wait for the user's next roadmap update.

**Never do, under any circumstance, even if it seems like it would unblock
you:** re-enable `_generate_career_jobs`, add any form of automated
LinkedIn access, insert placeholder/fake data into production JSON stores,
or claim a task is verified when you only reasoned about it rather than
ran it.

---

## 8A. ANTI-HALLUCINATION DISCIPLINE

The user needs to be able to trust your reports without re-checking
everything himself. Concretely:

- Never state a file exists, a function does something, or a bug is fixed
  without having actually opened/read/run the relevant code in **this**
  session. Don't rely on this document's descriptions as current fact —
  it was accurate as of when it was written; the code may have moved.
  Treat every claim in Sections 2, 3, and 5 as "last known state, reverify
  before acting on it," not gospel.
- If you're not sure whether something is true, say "I haven't verified
  this" rather than presenting a guess as fact.
- Never report test/verification results you didn't actually produce this
  session. Don't reuse or paraphrase a previous session's "63/63 passed"
  as if it still applies — rerun it.
- If a command fails, quote the actual error output in your report to the
  user, don't paraphrase it into something vaguer or more optimistic.
- Numbers matter here (job counts, scores, percentages) — never round up,
  estimate, or fill in a plausible-sounding number you didn't actually see
  in real output.

---

## 8B. UI REDESIGN GUIDANCE

The user wants to revisit the UI. Current state: single-file
`static/index.html`, "Liquid Glass Premium" theme (violet/azure/rose,
glassmorphism cards, animated WebGL gradient background), 5 pages per
Section 1. This went through ~5 rebuild rounds previously via external
design-export tools, which is part of why the codebase accumulated cruft.

Rules for this redesign:
- Confirm with the user what's changing — full redesign vs. targeted fixes
  (e.g. "the theme is fine, just fix X") — before rebuilding anything.
  This is a product/taste decision, not one to assume.
  This is the one category of task where you should present 2-3 concrete
  directions or mockups and let the user pick, rather than just building
  one version — UI taste is subjective in a way backend correctness isn't.
- Preserve all 5 pages' functional requirements from Section 1 exactly —
  redesign is visual/UX, not a feature-scope change, unless the user
  explicitly asks to change functionality too.
- Keep it a single static HTML/CSS/JS file unless the user asks to change
  that architecture.
- Verify each page actually renders and every interactive element (filters,
  buttons, tabs, modals) actually works after the change — per Step 3
  above, load it and check, don't just eyeball the markup.
- Do this incrementally, one page at a time, verified before moving to the
  next — not a single giant rewrite of all 5 pages at once, which is much
  harder to verify and debug if something breaks.

---

## 9. SUGGESTED IMMEDIATE ORDER OF WORK

1. Section 0 git identity/remote check — confirm with user before anything else.
2. Fix the Section 3 PENDING-scoring bug, on its own branch, off `master`.
   User verifies live.
3. Once verified, merge the good parts of `debug/gethired-stability`
   (Section 2's "genuinely good" list) + the Section 3 fix + the trivial
   `coderabbit-review` (`.coderabbit.yaml`) into `master`. User confirms
   before push.
4. Fix `sentence-transformers` not loading (venv/interpreter mismatch).
5. Install `feedparser`, get Naukri fetcher working.
6. UI redesign, per Section 8B — confirm direction with user first, then
   work through it page-by-page using the Section 7 loop.
7. From there, continue autonomously through Section 6's process rules and
   the Section 7 loop, picking each next task yourself and logging progress
   in `PROGRESS_LOG.md`, until the roadmap is exhausted or you hit a
   Section 8 stop condition.

Do not attempt items beyond step 3 until steps 1–3 are done and confirmed
working live by the user. From step 4 onward, you should not need the user
to hand you the next task — use `PROGRESS_LOG.md` and this document to
determine what's next yourself.
