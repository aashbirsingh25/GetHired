"""Indeed (and LinkedIn-extra) fetcher via JobSpy, run in an ISOLATED venv.

WHY ISOLATED: python-jobspy pins regex<2025 and old numpy - installing it in
the main venv broke sentence-transformers (the semantic scoring stack) two
different ways within minutes on 2026-09-06. It now lives in .venv-jobspy and
is called through a subprocess; the two dependency worlds never meet.

WHY AT ALL: our old direct Indeed fetcher has returned 0 for weeks (blocked).
JobSpy's Indeed path works and returns real posted dates + full descriptions
(verified live: 10/10 jobs with 4,700-char descriptions). Naukri via JobSpy
is captcha-blocked - Apify remains the Naukri door.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOBSPY_PY = os.path.join(BASE_DIR, ".venv-jobspy", "bin", "python")

_WORKER_SCRIPT = r"""
import json, sys
from jobspy import scrape_jobs
params = json.loads(sys.argv[1])
df = scrape_jobs(**params)
cols = ["title", "company", "location", "date_posted", "description", "job_url",
        "min_amount", "max_amount", "is_remote"]
out = []
for _, r in df.iterrows():
    out.append({c: (None if str(r.get(c)) in ("nan", "NaT", "None") else str(r.get(c))) for c in cols})
print(json.dumps(out))
"""


class JobFetcherList(list):
    def __init__(self, items=(), source_health=None):
        super().__init__(items)
        self.source_health = source_health or {
            "status": "success" if items else "zero_results",
            "message": f"Returned {len(items)} jobs",
            "http_status": 200,
            "jobs_count": len(items),
        }


def fetch_jobspy_indeed_jobs(role: str = "software engineer fresher",
                             max_results: int = 30,
                             return_metadata: bool = False):
    """Fresh (24h) India jobs from Indeed via the quarantined JobSpy env."""
    if not os.path.exists(JOBSPY_PY):
        health = {"status": "unconfigured", "message": ".venv-jobspy missing - run: python3 -m venv .venv-jobspy && .venv-jobspy/bin/pip install python-jobspy",
                  "http_status": None, "jobs_count": 0}
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    params = {"site_name": ["indeed"], "search_term": role, "location": "India",
              "results_wanted": max_results, "hours_old": 24, "country_indeed": "india"}
    try:
        proc = subprocess.run([JOBSPY_PY, "-c", _WORKER_SCRIPT, json.dumps(params)],
                              capture_output=True, text=True, timeout=120)
        rows = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else []
    except Exception as e:
        health = {"status": "unavailable", "message": f"jobspy subprocess failed: {str(e)[:80]}",
                  "http_status": None, "jobs_count": 0}
        print(f"[JobSpyIndeed] {health['message']}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    now_iso = datetime.now(timezone.utc).isoformat()
    jobs: List[Dict[str, Any]] = []
    for r in rows:
        title = (r.get("title") or "").strip()
        url = r.get("job_url") or ""
        if not title or not url:
            continue
        desc = (r.get("description") or "").strip()
        posted = r.get("date_posted")
        if posted and len(posted) == 10:
            posted = f"{posted}T00:00:00+00:00"
        jid = url.split("jk=")[-1][:20] if "jk=" in url else str(abs(hash(url)))[:14]
        jobs.append({
            "id": f"jobspy-indeed-{jid}",
            "title": title,
            "company": (r.get("company") or "Unknown").strip(),
            "location": (r.get("location") or "India").strip(),
            "url": url,
            "description": desc[:6000] or f"{title} - see posting.",
            "posted_date": posted,
            "source": "indeed",
            "extraction_method": "jobspy",
            "scan_timestamp": now_iso,
            "first_seen_at": now_iso,
            "closed": False,
            "needs_manual_link_review": False,
            "match": None,
        })
    print(f"[JobSpyIndeed] {len(jobs)} fresh (24h) India jobs")
    res = JobFetcherList(jobs)
    return {"status": "success", "health": res.source_health, "jobs": res} if return_metadata else res
