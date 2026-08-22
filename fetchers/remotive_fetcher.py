import os
import json
from datetime import datetime, timezone
from typing import List, Dict, Any

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# Remotive exposes a free public JSON API for remote jobs.
# Verified 2026-08-23: HTTP 200 without any key.
API_URL = "https://remotive.com/api/remote-jobs"

USER_AGENT = "GetHired-personal-job-search"

_ELIGIBLE_LOCATION_HINTS = (
    "india", "worldwide", "anywhere", "global", "remote", "apac", "asia",
)


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


def _location_eligible(loc_text: str) -> bool:
    loc = (loc_text or "").strip().lower()
    if not loc:
        return True
    return any(h in loc for h in _ELIGIBLE_LOCATION_HINTS)


def fetch_remotive_jobs(
    role: str = "Software Engineer",
    location: str = "Remote",
    max_results: int = 25,
    return_metadata: bool = False,
):
    config = load_config()
    cfg = config.get("job_boards", {}).get("remotive", {})

    if not cfg.get("enabled", True):
        health = {"status": "unconfigured", "message": "Remotive fetcher disabled in config", "http_status": None, "jobs_count": 0}
        print(f"[RemotiveFetcher] {health['message']}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    try:
        resp = requests.get(
            API_URL,
            params={"category": "software-dev", "limit": 100},
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        if resp.status_code != 200:
            status = "blocked" if resp.status_code in (403, 406, 429) else "unavailable"
            health = {"status": status, "message": f"Remotive returned HTTP {resp.status_code}", "http_status": resp.status_code, "jobs_count": 0}
            print(f"[RemotiveFetcher] {health['message']}")
            res = JobFetcherList([], source_health=health)
            return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

        entries = (resp.json() or {}).get("jobs", [])
        from job_identity import generate_canonical_job_id, normalize_url

        now_iso = datetime.now(timezone.utc).isoformat()
        jobs = []
        for idx, rj in enumerate(entries):
            if len(jobs) >= max_results:
                break
            try:
                title = (rj.get("title") or "").strip()
                link = (rj.get("url") or "").strip()
                if not title or not link:
                    continue
                if not _location_eligible(rj.get("candidate_required_location") or ""):
                    continue

                company = (rj.get("company_name") or "Remotive Listed Company").strip()
                tags = rj.get("tags") or []
                description = (rj.get("description") or "").strip()[:2000]
                if tags:
                    description = (description + " Tags: " + ", ".join(str(t) for t in tags)).strip()
                if not description:
                    description = f"{title} opportunity at {company}."

                raw_job = {
                    "title": title,
                    "company": company,
                    "location": (rj.get("candidate_required_location") or "Remote").strip() or "Remote",
                    "url": link,
                    "description": description,
                    "posted_date": (rj.get("publication_date") or now_iso),
                    "first_seen": now_iso,
                    "source": "remotive",
                    "source_id": str(rj.get("id") or ""),
                    "experience_required": "unspecified",
                    "salary_range_inr": (rj.get("salary") or "").strip() or "unspecified",
                    "parse_confidence": 0.95,
                    "parser_method": "remotive_api",
                }
                job_id = generate_canonical_job_id(raw_job)
                raw_job["id"] = job_id
                raw_job["job_id"] = job_id
                raw_job["canonical_url"] = normalize_url(link)
                jobs.append(raw_job)
            except Exception as entry_err:
                print(f"[RemotiveFetcher] Warning: failed parsing entry #{idx}: {entry_err}")
                continue

        h_status = "success" if jobs else "zero_results"
        health = {"status": h_status, "message": f"Fetched {len(jobs)} India-eligible jobs from Remotive", "http_status": 200, "jobs_count": len(jobs)}
        print(f"[RemotiveFetcher] Successfully fetched {len(jobs)} jobs from Remotive API.")
        res = JobFetcherList(jobs, source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    except requests.exceptions.Timeout as e:
        health = {"status": "timeout", "message": str(e), "http_status": None, "jobs_count": 0}
        print(f"[RemotiveFetcher] Timeout accessing Remotive: {e}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res
    except Exception as e:
        health = {"status": "unavailable", "message": str(e), "http_status": None, "jobs_count": 0}
        print(f"[RemotiveFetcher] Error accessing Remotive: {e}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res
