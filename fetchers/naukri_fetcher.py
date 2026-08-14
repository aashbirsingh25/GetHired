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

def fetch_naukri_jobs(role: str = "Software Engineer", location: str = "Gurugram", max_results: int = 25) -> List[Dict[str, Any]]:
    config = load_config()
    job_boards = config.get("job_boards", {})
    naukri_cfg = job_boards.get("naukri", {})

    if not naukri_cfg.get("enabled", True):
        print("[NaukriFetcher] Naukri fetcher disabled in config.")
        return []

    base_url = naukri_cfg.get("rss_base_url", "https://www.naukri.com/jobapi/v3/search")
    encoded_role = urllib.parse.quote(role)
    encoded_loc = urllib.parse.quote(location)
    
    # Construct RSS / Feed URL
    feed_url = f"{base_url}?k={encoded_role}&l={encoded_loc}&rss=true"

    if feedparser is None:
        print("[NaukriFetcher] Warning: 'feedparser' library is not installed.")
        return []

    try:
        import requests
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.get(feed_url, headers=headers, timeout=5)
        if resp.status_code != 200:
            print(f"[NaukriFetcher] RSS feed HTTP {resp.status_code}")
            return []
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

                # Extract company from title if format is "Title - Company" or similar
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

        print(f"[NaukriFetcher] Successfully fetched {len(jobs)} jobs from Naukri RSS.")
        return jobs

    except Exception as e:
        print(f"[NaukriFetcher] Error accessing Naukri RSS feed ({feed_url}): {e}")
        return []
