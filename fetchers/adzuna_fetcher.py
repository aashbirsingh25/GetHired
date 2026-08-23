import os
import json
import time
from datetime import datetime, timezone
from typing import List, Dict, Any

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# Adzuna aggregates many job boards and has a proper free API (app_id +
# app_key). India endpoint: /v1/api/jobs/in/search/<page>.
# Free tier is rate-limited, so this fetcher rotates a small slice of queries
# per cycle rather than querying everything every time.
API = "https://api.adzuna.com/v1/api/jobs/in/search/1"
USER_AGENT = "GetHired-personal-job-search"
CALL_DELAY_SECONDS = 1.0

# Kept deliberately short: each entry costs one API call per cycle it runs.
QUERIES = [
    ("software engineer fresher", None),
    ("software developer fresher", None),
    ("graduate engineer trainee", None),
    ("software engineer intern", None),
    ("entry level software engineer", None),
    ("software engineer", "Bangalore"),
    ("software engineer", "Gurgaon"),
    ("software developer", "Noida"),
    ("software engineer", "Hyderabad"),
    ("python developer fresher", None),
]


def load_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


class JobFetcherList(list):
    """List subclass that attaches source health metadata without breaking list compatibility."""

    def __init__(self, items=(), source_health=None):
        super().__init__(items)
        self.source_health = source_health or {
            "status": "success" if items else "zero_results",
            "message": f"Returned {len(items)} jobs",
            "http_status": 200,
            "jobs_count": len(items),
        }


def _to_job(r: Dict[str, Any], now_iso: str) -> Dict[str, Any]:
    from job_identity import generate_canonical_job_id, normalize_url

    title = (r.get("title") or "").strip()
    url = (r.get("redirect_url") or "").strip()
    if not title or not url:
        return {}
    company = ((r.get("company") or {}).get("display_name") or "Adzuna Listed Company").strip()
    location = ((r.get("location") or {}).get("display_name") or "India").strip()
    desc = (r.get("description") or "").strip()

    salary = "unspecified"
    smin, smax = r.get("salary_min"), r.get("salary_max")
    if smin and smax:
        salary = f"INR {int(smin):,} - {int(smax):,}"

    job = {
        "title": title,
        "company": company,
        "location": location,
        "url": url,
        "description": desc or f"{title} at {company}.",
        "posted_date": r.get("created") or now_iso,
        "first_seen": now_iso,
        "source": "adzuna",
        "source_id": str(r.get("id") or ""),
        "experience_required": "unspecified",
        "salary_range_inr": salary,
        "parse_confidence": 0.90,
        "parser_method": "adzuna_api",
    }
    jid = generate_canonical_job_id(job)
    job["id"] = jid
    job["job_id"] = jid
    job["canonical_url"] = normalize_url(url)
    return job


def fetch_adzuna_jobs(role: str = "Software Engineer", location: str = "India",
                      max_results: int = 60, queries_per_cycle: int = 3,
                      cycle_seed: int = 0, return_metadata: bool = False):
    """Fetch India jobs from Adzuna, rotating a slice of QUERIES per cycle."""
    config = load_config()
    cfg = config.get("job_boards", {}).get("adzuna", {})
    app_id = os.environ.get("ADZUNA_APP_ID", "").strip()
    app_key = os.environ.get("ADZUNA_APP_KEY", "").strip()

    if not cfg.get("enabled", True) or not app_id or not app_key:
        reason = "Adzuna fetcher disabled in config" if not cfg.get("enabled", True) \
            else "ADZUNA_APP_ID/ADZUNA_APP_KEY not set"
        health = {"status": "unconfigured", "message": reason, "http_status": None, "jobs_count": 0}
        print(f"[AdzunaFetcher] {reason}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    n = max(1, queries_per_cycle)
    start = (cycle_seed * n) % len(QUERIES)
    picked = [QUERIES[(start + i) % len(QUERIES)] for i in range(n)]

    now_iso = datetime.now(timezone.utc).isoformat()
    seen, jobs = set(), []
    last_status = None
    try:
        for what, where in picked:
            if len(jobs) >= max_results:
                break
            params = {
                "app_id": app_id, "app_key": app_key,
                "results_per_page": 30, "what": what,
                "max_days_old": 30, "content-type": "application/json",
            }
            if where:
                params["where"] = where
            try:
                r = requests.get(API, params=params, headers={"User-Agent": USER_AGENT}, timeout=20)
                last_status = r.status_code
                if r.status_code != 200:
                    print(f"[AdzunaFetcher] '{what}' -> HTTP {r.status_code}")
                    continue
                for raw in (r.json() or {}).get("results", []):
                    job = _to_job(raw, now_iso)
                    if job and job["id"] not in seen:
                        seen.add(job["id"])
                        jobs.append(job)
                        if len(jobs) >= max_results:
                            break
            except Exception as e:
                print(f"[AdzunaFetcher] '{what}' failed: {str(e)[:80]}")
            time.sleep(CALL_DELAY_SECONDS)

        status = "success" if jobs else ("blocked" if last_status in (401, 403, 429) else "zero_results")
        health = {"status": status, "message": f"Fetched {len(jobs)} jobs from {len(picked)} Adzuna queries",
                  "http_status": last_status or 200, "jobs_count": len(jobs)}
        print(f"[AdzunaFetcher] Successfully fetched {len(jobs)} jobs "
              f"(queries {start}-{start + n - 1} of {len(QUERIES)}).")
        res = JobFetcherList(jobs, source_health=health)
        return {"status": status, "health": health, "jobs": res} if return_metadata else res
    except Exception as e:
        health = {"status": "unavailable", "message": str(e), "http_status": None, "jobs_count": 0}
        print(f"[AdzunaFetcher] Error: {e}")
        res = JobFetcherList(jobs, source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res
