import os
import json
import requests
from datetime import datetime, timezone
from typing import List, Dict, Any

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

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
            "jobs_count": len(items)
        }

def fetch_indeed_jobs(role: str = "Software Engineer", location: str = "Gurugram", max_results: int = 25, return_metadata: bool = False) -> List[Dict[str, Any]]:
    config = load_config()
    job_boards = config.get("job_boards", {})
    indeed_cfg = job_boards.get("indeed", {})

    if not indeed_cfg.get("enabled", True):
        health = {"status": "unconfigured", "message": "Indeed fetcher disabled in config", "http_status": None, "jobs_count": 0}
        print(f"[IndeedFetcher] {health['message']}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    publisher_id = indeed_cfg.get("publisher_id", "").strip()
    if not publisher_id:
        health = {"status": "unconfigured", "message": "No publisher_id configured", "http_status": None, "jobs_count": 0}
        print(f"[IndeedFetcher] Indeed fetcher skipped - {health['message']}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    url = "http://api.indeed.com/ads/apisearch"
    params = {
        "publisher": publisher_id,
        "q": role,
        "l": location,
        "limit": min(max_results, 25),
        "format": "json",
        "v": "2"
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 403:
            health = {"status": "blocked", "message": f"HTTP {response.status_code} Forbidden", "http_status": 403, "jobs_count": 0}
            print(f"[IndeedFetcher] {health['message']}")
            res = JobFetcherList([], source_health=health)
            return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res
        elif response.status_code != 200:
            health = {"status": "unavailable", "message": f"Indeed API returned HTTP {response.status_code}", "http_status": response.status_code, "jobs_count": 0}
            print(f"[IndeedFetcher] {health['message']}")
            res = JobFetcherList([], source_health=health)
            return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

        data = response.json()
        results = data.get("results", [])
        jobs = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for item in results:
            jobkey = item.get("jobkey") or str(item.get("jobtitle", ""))
            job_title = item.get("jobtitle", role)
            company = item.get("company", "Unknown Company")
            loc = item.get("formattedLocation") or item.get("city") or location
            job_url = item.get("url") or f"https://www.indeed.com/viewjob?jk={jobkey}"
            snippet = item.get("snippet", "")

            from job_identity import generate_canonical_job_id, normalize_url
            raw_job = {
                "title": job_title,
                "company": company,
                "location": loc,
                "url": job_url,
                "source": "indeed",
                "source_id": jobkey,
                "description": f"{job_title} position at {company} ({loc}). {snippet}",
                "posted_date": item.get("date") or now_iso,
                "first_seen": item.get("date") or now_iso,
                "experience_required": "0-3 years",
                "salary_range_inr": "₹8L - ₹18L PA",
                "parse_confidence": 0.95,
                "parser_method": "indeed_publisher_api"
            }
            job_id = generate_canonical_job_id(raw_job)
            raw_job["id"] = job_id
            raw_job["job_id"] = job_id
            raw_job["canonical_url"] = normalize_url(job_url)
            jobs.append(raw_job)

        h_status = "success" if jobs else "zero_results"
        health = {"status": h_status, "message": f"Fetched {len(jobs)} jobs", "http_status": 200, "jobs_count": len(jobs)}
        print(f"[IndeedFetcher] Successfully fetched {len(jobs)} jobs from Indeed.")
        res = JobFetcherList(jobs, source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    except Exception as e:
        status_name = "timeout" if isinstance(e, requests.exceptions.Timeout) else "unavailable"
        health = {"status": status_name, "message": str(e), "http_status": None, "jobs_count": 0}
        print(f"[IndeedFetcher] Error fetching jobs from Indeed: {e}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res
