"""Verify that feed jobs are still accepting applications; mark dead ones closed.

WHY: jobs are snapshots. Nothing re-checked them after capture, so the #1 feed
job (98% match) was an Internshala internship that had closed days earlier -
the user clicked through to a dead page showing unrelated 5+ years roles.
The pipeline already drops jobs with closed=True; this loop is what finally
SETS that flag.

Detection is deliberately conservative - a job is only marked closed on
strong evidence (HTTP 404/410, or an explicit closed-marker phrase verified
on real dead pages). Anything ambiguous stays live; a false "closed" hides a
real opportunity, which is worse than a stale row surviving one more cycle.

Verified markers (live, 2026-09-05, internshala dead page):
  - 'Applications are closed for this internship'
  - id="status" value="expired"
  - 'Closed for applications'
"""
import json
import os
import re
import threading
import time
import urllib.request
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_FILE = os.path.join(BASE_DIR, "jobs_store.json")
STATE_FILE = os.path.join(BASE_DIR, "liveness_state.json")

CHECK_INTERVAL_S = 25           # one URL fetch per 25s
RECHECK_EVERY_H = 72            # each job re-verified every 3 days
DAILY_CAP = 250
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

# Explicit, low-false-positive phrases meaning "this posting is dead".
CLOSED_PATTERNS = [
    r"applications?\s+are\s+closed\s+for\s+this",
    r'id="status"\s+value="expired"',
    r"closed\s+for\s+applications",
    r"no\s+longer\s+accepting\s+applications",
    r"this\s+(?:job|position|posting|role)\s+(?:is\s+)?no\s+longer\s+(?:available|active|open)",
    r"position\s+has\s+been\s+filled",
    r"this\s+(?:job|vacancy)\s+has\s+expired",
    r"job\s+posting\s+(?:is\s+)?(?:closed|expired)",
    r"sorry,?\s+this\s+job\s+(?:is\s+)?(?:closed|expired|unavailable)",
]
_CLOSED_RE = re.compile("|".join(CLOSED_PATTERNS), re.IGNORECASE)


def _load_state():
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception:
        return {"date": "", "checked_today": 0, "checked_at": {}}


def _save_state(st):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_FILE)


def check_job_alive(url):
    """Returns (alive: bool, reason: str). Errs on the side of 'alive'."""
    try:
        from hardened_fetch import hardened_get
        r = hardened_get(url, timeout=20)
        if r.status_code in (404, 410):
            return False, f"HTTP {r.status_code}"
        if r.status_code != 200:
            return True, f"inconclusive HTTP {r.status_code}"
        html = r.text
    except Exception as e:
        return True, f"inconclusive: {str(e)[:60]}"
    m = _CLOSED_RE.search(html)
    if m:
        return False, f"closed marker: '{m.group(0)[:50]}'"
    return True, "no closed markers"


CLOSED_FILE = os.path.join(BASE_DIR, "closed_jobs.json")


def _mark_closed(job_id, reason):
    """Record closure in a file OWNED by this checker.

    First version set closed=True on the job row in jobs_store.json - the
    scanner rewrites that file every few seconds from its own in-memory rows,
    and all three flags from the first sweep were silently clobbered within
    minutes. A separate one-writer file has no such race; the pipeline
    consults it when filtering."""
    try:
        data = json.load(open(CLOSED_FILE, encoding="utf-8"))
    except Exception:
        data = {}
    data[job_id] = {"reason": reason, "at": datetime.now(timezone.utc).isoformat()}
    tmp = CLOSED_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, CLOSED_FILE)


def liveness_loop():
    time.sleep(480)  # let startup settle; enricher starts at 600s, offset them
    while True:
        try:
            st = _load_state()
            today = datetime.now().strftime("%Y-%m-%d")
            if st.get("date") != today:
                st.update(date=today, checked_today=0)
            if st["checked_today"] >= DAILY_CAP:
                time.sleep(3600)
                continue

            jobs = json.load(open(JOBS_FILE, encoding="utf-8")).get("jobs", [])
            now = time.time()
            cutoff = now - RECHECK_EVERY_H * 3600
            checked_at = st.get("checked_at", {})
            cands = [j for j in jobs
                     if not j.get("closed")
                     and (j.get("url") or "").startswith("http")
                     and ((j.get("match") or {}).get("score") or 0) >= 50
                     and checked_at.get(j.get("id"), 0) < cutoff]
            # highest score first: the top of the feed must never be a ghost
            cands.sort(key=lambda j: -((j.get("match") or {}).get("score") or 0))
            if not cands:
                time.sleep(1800)
                continue

            job = cands[0]
            alive, reason = check_job_alive(job["url"])
            checked_at[job["id"]] = now
            st["checked_at"] = {k: v for k, v in checked_at.items() if v > now - 30 * 86400}
            st["checked_today"] += 1
            if not alive:
                _mark_closed(job["id"], reason)
                print(f"[Liveness] CLOSED: '{job.get('title','')[:40]}' @ {job.get('company','')[:20]} ({reason})")
            _save_state(st)
        except Exception as e:
            print(f"[Liveness] loop error: {str(e)[:100]}")
        time.sleep(CHECK_INTERVAL_S)


def start_liveness_thread():
    t = threading.Thread(target=liveness_loop, daemon=True, name="job-liveness")
    t.start()
    return t
