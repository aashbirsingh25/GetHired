import os
import re
import json
import time
from datetime import datetime, timezone
from typing import List, Dict, Any

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# Shine.com (Indian job board, ~20k results for a single fresher software
# query). Listing pages are server-rendered Next.js: the result set is
# embedded in <script id="__NEXT_DATA__">, so no key and no browser needed.
# Verified 2026-08-24: HTTP 200, 20 jobs/page with experience ranges.
BASE = "https://www.shine.com"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
PAGE_DELAY_SECONDS = 1.5

# Search paths that target fresher/entry-level software roles.
SEARCH_PATHS = [
    "/job-search/fresher-software-jobs",
    "/job-search/software-engineer-fresher-jobs",
    "/job-search/software-developer-fresher-jobs",
    "/job-search/graduate-engineer-trainee-jobs",
    "/job-search/software-trainee-jobs",
    "/job-search/junior-software-developer-jobs",
    "/job-search/software-engineer-jobs-in-bangalore",
    "/job-search/software-engineer-jobs-in-gurgaon",
    "/job-search/software-engineer-jobs-in-noida",
    "/job-search/software-engineer-jobs-in-hyderabad",
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


def _slug(s: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (s or "").lower())).strip("-")


def _results_from_html(html: str) -> List[Dict[str, Any]]:
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        return []
    try:
        d = json.loads(m.group(1))
        state = (((d.get("props") or {}).get("pageProps") or {}).get("initialState") or {})
        data = ((state.get("jsrp") or {}).get("searchresult") or {}).get("data") or {}
        results = data.get("results")
        return results if isinstance(results, list) else []
    except Exception:
        return []


def _to_job(raw: Dict[str, Any], now_iso: str) -> Dict[str, Any]:
    from job_identity import generate_canonical_job_id, normalize_url

    title = (raw.get("jJT") or "").strip()
    company = (raw.get("jCName") or "Shine Listed Company").strip()
    if not title:
        return {}
    locs = raw.get("jLoc") or []
    location = ", ".join(str(l) for l in locs if l) if isinstance(locs, list) else str(locs or "India")
    job_ref = raw.get("id")
    url = f"{BASE}/jobs/{_slug(title)}/{_slug(company)}/{job_ref}" if job_ref else BASE

    exp = (raw.get("jExp") or "").strip()          # e.g. "0 to 3 Yrs"
    salary = (raw.get("jSal") or "").strip()
    industry = (raw.get("jInd") or "").strip()
    desc = " ".join(x for x in [
        f"{title} at {company}.",
        f"Experience: {exp}." if exp else "",
        f"Industry: {industry}." if industry else "",
    ] if x)

    job = {
        "title": title,
        "company": company,
        "location": location or "India",
        "url": url,
        "description": desc,
        "posted_date": raw.get("jPDate") or now_iso,
        "first_seen": now_iso,
        "source": "shine",
        "source_id": str(job_ref or ""),
        "experience_required": exp or "unspecified",
        "salary_range_inr": salary or "unspecified",
        "parse_confidence": 0.90,
        "parser_method": "shine_next_data",
    }
    jid = generate_canonical_job_id(job)
    job["id"] = jid
    job["job_id"] = jid
    job["canonical_url"] = normalize_url(url)
    return job


def fetch_shine_jobs(role: str = "Software Engineer", location: str = "India",
                     max_results: int = 40, paths_per_cycle: int = 3,
                     cycle_seed: int = 0, return_metadata: bool = False):
    """Fetch fresher-focused listings from Shine.

    Rotates a slice of SEARCH_PATHS per cycle so coverage spreads over time
    rather than hitting every path every run.
    """
    config = load_config()
    cfg = config.get("job_boards", {}).get("shine", {})
    if not cfg.get("enabled", True):
        health = {"status": "unconfigured", "message": "Shine fetcher disabled in config",
                  "http_status": None, "jobs_count": 0}
        print(f"[ShineFetcher] {health['message']}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    n = max(1, paths_per_cycle)
    start = (cycle_seed * n) % len(SEARCH_PATHS)
    picked = [SEARCH_PATHS[(start + i) % len(SEARCH_PATHS)] for i in range(n)]

    now_iso = datetime.now(timezone.utc).isoformat()
    seen, jobs = set(), []
    last_status = None
    try:
        for path in picked:
            if len(jobs) >= max_results:
                break
            try:
                r = requests.get(BASE + path, headers={"User-Agent": USER_AGENT}, timeout=20)
                last_status = r.status_code
                if r.status_code != 200:
                    print(f"[ShineFetcher] {path} -> HTTP {r.status_code}")
                    continue
                for raw in _results_from_html(r.text):
                    job = _to_job(raw, now_iso)
                    if job and job["id"] not in seen:
                        seen.add(job["id"])
                        jobs.append(job)
                        if len(jobs) >= max_results:
                            break
            except Exception as e:
                print(f"[ShineFetcher] {path} failed: {str(e)[:80]}")
            time.sleep(PAGE_DELAY_SECONDS)

        status = "success" if jobs else ("blocked" if last_status in (403, 429) else "zero_results")
        health = {"status": status, "message": f"Fetched {len(jobs)} jobs from {len(picked)} Shine searches",
                  "http_status": last_status or 200, "jobs_count": len(jobs)}
        print(f"[ShineFetcher] Successfully fetched {len(jobs)} jobs "
              f"(paths {start}-{start + n - 1} of {len(SEARCH_PATHS)}).")
        res = JobFetcherList(jobs, source_health=health)
        return {"status": status, "health": health, "jobs": res} if return_metadata else res
    except Exception as e:
        health = {"status": "unavailable", "message": str(e), "http_status": None, "jobs_count": 0}
        print(f"[ShineFetcher] Error: {e}")
        res = JobFetcherList(jobs, source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res
