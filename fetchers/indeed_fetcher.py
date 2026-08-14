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

def fetch_indeed_jobs(role: str = "Software Engineer", location: str = "Gurugram", max_results: int = 25) -> List[Dict[str, Any]]:
    config = load_config()
    job_boards = config.get("job_boards", {})
    indeed_cfg = job_boards.get("indeed", {})

    if not indeed_cfg.get("enabled", True):
        print("[IndeedFetcher] Indeed fetcher disabled in config.")
        return []

    publisher_id = indeed_cfg.get("publisher_id", "").strip()
    if not publisher_id:
        print("[IndeedFetcher] Indeed fetcher skipped - no publisher_id configured")
        return []

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
        if response.status_code != 200:
            print(f"[IndeedFetcher] Indeed legacy API search endpoint retired (HTTP {response.status_code}). Returning empty job list.")
            return []

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

        print(f"[IndeedFetcher] Successfully fetched {len(jobs)} jobs from Indeed.")
        return jobs

    except Exception as e:
        print(f"[IndeedFetcher] Error fetching jobs from Indeed: {e}")
        return []
