import os
import json
from datetime import datetime, timezone
from typing import List, Dict, Any

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# RemoteOK exposes a free public JSON API (first element is a legal notice,
# the rest are job dicts). Verified 2026-08-23: HTTP 200, 100 jobs, no key.
API_URL = "https://remoteok.com/api"

USER_AGENT = "GetHired-personal-job-search"

# Remote boards are global; only keep roles an India-based candidate can hold.
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
        return True  # unspecified: assume open
    return any(h in loc for h in _ELIGIBLE_LOCATION_HINTS)


def fetch_remoteok_jobs(
    role: str = "Software Engineer",
    location: str = "Remote",
    max_results: int = 25,
    return_metadata: bool = False,
):
    config = load_config()
    cfg = config.get("job_boards", {}).get("remoteok", {})

    if not cfg.get("enabled", True):
        health = {"status": "unconfigured", "message": "RemoteOK fetcher disabled in config", "http_status": None, "jobs_count": 0}
        print(f"[RemoteOKFetcher] {health['message']}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    try:
        resp = requests.get(API_URL, headers={"User-Agent": USER_AGENT}, timeout=20)
        if resp.status_code != 200:
            status = "blocked" if resp.status_code in (403, 406, 429) else "unavailable"
            health = {"status": status, "message": f"RemoteOK returned HTTP {resp.status_code}", "http_status": resp.status_code, "jobs_count": 0}
            print(f"[RemoteOKFetcher] {health['message']}")
            res = JobFetcherList([], source_health=health)
            return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

        entries = resp.json()
        from job_identity import generate_canonical_job_id, normalize_url

        now_iso = datetime.now(timezone.utc).isoformat()
        jobs = []
        for idx, rj in enumerate(entries):
            if len(jobs) >= max_results:
                break
            try:
                if not isinstance(rj, dict):
                    continue  # first element is a legal notice
                title = (rj.get("position") or "").strip()
                link = (rj.get("url") or "").strip()
                if not title or not link:
                    continue
                if not _location_eligible(rj.get("location") or ""):
                    continue

                company = (rj.get("company") or "RemoteOK Listed Company").strip()
                tags = rj.get("tags") or []
                description = (rj.get("description") or "").strip()[:2000]
                if tags:
                    description = (description + " Tags: " + ", ".join(str(t) for t in tags)).strip()
                if not description:
                    description = f"{title} opportunity at {company}."

                salary = ""
                if rj.get("salary_min") and rj.get("salary_max"):
                    salary = f"${rj['salary_min']}-${rj['salary_max']}"

                raw_job = {
                    "title": title,
                    "company": company,
                    "location": (rj.get("location") or "Remote").strip() or "Remote",
                    "url": link,
                    "description": description,
                    "posted_date": (rj.get("date") or now_iso),
                    "first_seen": now_iso,
                    "source": "remoteok",
                    "source_id": str(rj.get("id") or rj.get("slug") or ""),
                    "experience_required": "unspecified",
                    "salary_range_inr": salary or "unspecified",
                    "parse_confidence": 0.95,
                    "parser_method": "remoteok_api",
                }
                job_id = generate_canonical_job_id(raw_job)
                raw_job["id"] = job_id
                raw_job["job_id"] = job_id
                raw_job["canonical_url"] = normalize_url(link)
                jobs.append(raw_job)
            except Exception as entry_err:
                print(f"[RemoteOKFetcher] Warning: failed parsing entry #{idx}: {entry_err}")
                continue

        h_status = "success" if jobs else "zero_results"
        health = {"status": h_status, "message": f"Fetched {len(jobs)} India-eligible jobs from RemoteOK", "http_status": 200, "jobs_count": len(jobs)}
        print(f"[RemoteOKFetcher] Successfully fetched {len(jobs)} jobs from RemoteOK API.")
        res = JobFetcherList(jobs, source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    except requests.exceptions.Timeout as e:
        health = {"status": "timeout", "message": str(e), "http_status": None, "jobs_count": 0}
        print(f"[RemoteOKFetcher] Timeout accessing RemoteOK: {e}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res
    except Exception as e:
        health = {"status": "unavailable", "message": str(e), "http_status": None, "jobs_count": 0}
        print(f"[RemoteOKFetcher] Error accessing RemoteOK: {e}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res
