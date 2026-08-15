import os
import json
import urllib.parse
from datetime import datetime, timezone
from typing import List, Dict, Any

try:
    import feedparser
except ImportError:
    feedparser = None

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

def fetch_naukri_jobs(role: str = "Software Engineer", location: str = "Gurugram", max_results: int = 25, return_metadata: bool = False) -> List[Dict[str, Any]]:
    config = load_config()
    job_boards = config.get("job_boards", {})
    naukri_cfg = job_boards.get("naukri", {})

    if not naukri_cfg.get("enabled", True):
        health = {"status": "unconfigured", "message": "Naukri fetcher disabled in config", "http_status": None, "jobs_count": 0}
        print(f"[NaukriFetcher] {health['message']}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    base_url = naukri_cfg.get("rss_base_url", "https://www.naukri.com/jobapi/v3/search")
    encoded_role = urllib.parse.quote(role)
    encoded_loc = urllib.parse.quote(location)
    
    feed_url = f"{base_url}?k={encoded_role}&l={encoded_loc}&rss=true"

    if feedparser is None:
        health = {"status": "unconfigured", "message": "'feedparser' library is not installed", "http_status": None, "jobs_count": 0}
        print(f"[NaukriFetcher] Warning: {health['message']}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    try:
        import requests
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.get(feed_url, headers=headers, timeout=5)
        if resp.status_code == 403:
            health = {"status": "blocked", "message": f"HTTP {resp.status_code} Forbidden by anti-bot", "http_status": 403, "jobs_count": 0}
            print(f"[NaukriFetcher] {health['message']}")
            res = JobFetcherList([], source_health=health)
            return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res
        elif resp.status_code != 200:
            health = {"status": "unavailable", "message": f"Naukri RSS feed returned HTTP {resp.status_code}", "http_status": resp.status_code, "jobs_count": 0}
            print(f"[NaukriFetcher] {health['message']}")
            res = JobFetcherList([], source_health=health)
            return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

        parsed_feed = feedparser.parse(resp.content)
        entries = getattr(parsed_feed, "entries", [])
        
        jobs = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for idx, entry in enumerate(entries[:max_results]):
            try:
                title = entry.get("title", "")
                link = entry.get("link", "")
                summary = entry.get("summary", "") or entry.get("description", "")
                published = entry.get("published", "") or now_iso

                if not title or not link:
                    print(f"[NaukriFetcher] Skipping entry #{idx}: missing required title or link.")
                    continue

                company = "Naukri Listed Partner"
                if " - " in title:
                    parts = title.split(" - ")
                    title = parts[0].strip()
                    company = parts[1].strip()
                elif " at " in title:
                    parts = title.split(" at ")
                    title = parts[0].strip()
                    company = parts[1].strip()

                from job_identity import generate_canonical_job_id, normalize_url
                raw_job = {
                    "title": title,
                    "company": company,
                    "location": location,
                    "url": link,
                    "description": summary or f"{title} opportunity at {company}.",
                    "posted_date": published,
                    "first_seen": now_iso,
                    "source": "naukri",
                    "source_id": entry.get("id"),
                    "experience_required": "0-2 years",
                    "salary_range_inr": "₹6L - ₹15L PA",
                    "parse_confidence": 0.90,
                    "parser_method": "naukri_rss_feed"
                }
                job_id = generate_canonical_job_id(raw_job)
                raw_job["id"] = job_id
                raw_job["job_id"] = job_id
                raw_job["canonical_url"] = normalize_url(link)
                jobs.append(raw_job)
            except Exception as entry_err:
                print(f"[NaukriFetcher] Warning: Failed parsing RSS entry #{idx}: {entry_err}")
                continue

        h_status = "success" if jobs else "zero_results"
        health = {"status": h_status, "message": f"Fetched {len(jobs)} jobs from Naukri", "http_status": 200, "jobs_count": len(jobs)}
        print(f"[NaukriFetcher] Successfully fetched {len(jobs)} jobs from Naukri RSS.")
        res = JobFetcherList(jobs, source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    except Exception as e:
        status_name = "timeout" if isinstance(e, requests.exceptions.Timeout) else "unavailable"
        health = {"status": status_name, "message": str(e), "http_status": None, "jobs_count": 0}
        print(f"[NaukriFetcher] Error accessing Naukri RSS feed ({feed_url}): {e}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res
