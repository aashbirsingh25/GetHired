import os
import json
import time
from datetime import datetime, timezone
from typing import List, Dict, Any

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
BUDGET_FILE = os.path.join(BASE_DIR, "jooble_usage.json")

# Jooble aggregator API. IMPORTANT: the free key has a documented default
# limit of 500 REQUESTS TOTAL (per the signup email), which is tiny compared
# to our other sources. So this fetcher:
#   - makes at most JOOBLE_CALLS_PER_CYCLE calls per cycle (default 1),
#   - tracks lifetime usage in jooble_usage.json and refuses to exceed
#     JOOBLE_TOTAL_BUDGET, leaving headroom for manual testing.
# It is a supplementary source, not a primary one.
API = "https://jooble.org/api/"
USER_AGENT = "GetHired-personal-job-search"
JOOBLE_CALLS_PER_CYCLE = 1
JOOBLE_TOTAL_BUDGET = 400

QUERIES = [
    ("software engineer fresher", "India"),
    ("software developer fresher", "India"),
    ("graduate engineer trainee", "India"),
    ("software engineer intern", "Bangalore"),
    ("software developer intern", "Gurgaon"),
    ("entry level software", "Noida"),
    ("python developer fresher", "India"),
    ("java developer fresher", "India"),
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


def _usage() -> Dict[str, Any]:
    if os.path.exists(BUDGET_FILE):
        try:
            return json.load(open(BUDGET_FILE, encoding="utf-8"))
        except Exception:
            pass
    return {"calls_made": 0, "first_call": None, "last_call": None}


def _record_call(n: int = 1) -> None:
    u = _usage()
    now = datetime.now(timezone.utc).isoformat()
    u["calls_made"] = int(u.get("calls_made", 0)) + n
    u["first_call"] = u.get("first_call") or now
    u["last_call"] = now
    tmp = BUDGET_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(u, f, indent=2)
    os.replace(tmp, BUDGET_FILE)


def _to_job(r: Dict[str, Any], now_iso: str) -> Dict[str, Any]:
    from job_identity import generate_canonical_job_id, normalize_url

    title = (r.get("title") or "").strip()
    url = (r.get("link") or "").strip()
    if not title or not url:
        return {}
    job = {
        "title": title,
        "company": (r.get("company") or "Jooble Listed Company").strip(),
        "location": (r.get("location") or "India").strip(),
        "url": url,
        "description": (r.get("snippet") or f"{title} (via Jooble)").strip(),
        "posted_date": r.get("updated") or now_iso,
        "first_seen": now_iso,
        "source": "jooble",
        "source_id": str(r.get("id") or ""),
        "experience_required": "unspecified",
        "salary_range_inr": (r.get("salary") or "").strip() or "unspecified",
        "parse_confidence": 0.85,
        "parser_method": "jooble_api",
    }
    jid = generate_canonical_job_id(job)
    job["id"] = jid
    job["job_id"] = jid
    job["canonical_url"] = normalize_url(url)
    return job


def fetch_jooble_jobs(role: str = "Software Engineer", location: str = "India",
                      max_results: int = 40, cycle_seed: int = 0,
                      return_metadata: bool = False):
    """Fetch a small slice from Jooble, respecting the 500-request lifetime cap."""
    config = load_config()
    cfg = config.get("job_boards", {}).get("jooble", {})
    key = os.environ.get("JOOBLE_API_KEY", "").strip()

    if not cfg.get("enabled", True) or not key:
        reason = "Jooble fetcher disabled in config" if not cfg.get("enabled", True) \
            else "JOOBLE_API_KEY not set"
        health = {"status": "unconfigured", "message": reason, "http_status": None, "jobs_count": 0}
        print(f"[JoobleFetcher] {reason}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    used = int(_usage().get("calls_made", 0))
    budget = int(cfg.get("total_budget", JOOBLE_TOTAL_BUDGET))
    if used >= budget:
        msg = f"Jooble lifetime call budget exhausted ({used}/{budget}) - skipping"
        print(f"[JoobleFetcher] {msg}")
        health = {"status": "quota_exhausted", "message": msg, "http_status": None, "jobs_count": 0}
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    calls = min(int(cfg.get("calls_per_cycle", JOOBLE_CALLS_PER_CYCLE)), budget - used)
    start = (cycle_seed * calls) % len(QUERIES)
    picked = [QUERIES[(start + i) % len(QUERIES)] for i in range(max(1, calls))]

    now_iso = datetime.now(timezone.utc).isoformat()
    seen, jobs = set(), []
    last_status = None
    try:
        for keywords, loc in picked:
            try:
                r = requests.post(API + key, json={"keywords": keywords, "location": loc, "page": "1"},
                                  headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
                                  timeout=20)
                _record_call(1)
                last_status = r.status_code
                if r.status_code != 200:
                    print(f"[JoobleFetcher] '{keywords}' -> HTTP {r.status_code}")
                    continue
                for raw in (r.json() or {}).get("jobs", []):
                    job = _to_job(raw, now_iso)
                    if job and job["id"] not in seen:
                        seen.add(job["id"])
                        jobs.append(job)
                        if len(jobs) >= max_results:
                            break
            except Exception as e:
                print(f"[JoobleFetcher] '{keywords}' failed: {str(e)[:80]}")
            time.sleep(1.0)

        used_after = int(_usage().get("calls_made", 0))
        status = "success" if jobs else ("blocked" if last_status in (401, 403, 429) else "zero_results")
        health = {"status": status,
                  "message": f"Fetched {len(jobs)} jobs; lifetime calls {used_after}/{budget}",
                  "http_status": last_status or 200, "jobs_count": len(jobs)}
        print(f"[JoobleFetcher] Fetched {len(jobs)} jobs (lifetime calls {used_after}/{budget}).")
        res = JobFetcherList(jobs, source_health=health)
        return {"status": status, "health": health, "jobs": res} if return_metadata else res
    except Exception as e:
        health = {"status": "unavailable", "message": str(e), "http_status": None, "jobs_count": 0}
        print(f"[JoobleFetcher] Error: {e}")
        res = JobFetcherList(jobs, source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res
