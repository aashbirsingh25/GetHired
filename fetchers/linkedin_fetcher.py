import os
import json
import time
import urllib.parse
from datetime import datetime, timezone
from typing import List, Dict, Any

import requests
from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# LinkedIn PUBLIC guest job search (logged-out endpoint, same content as
# linkedin.com/jobs without an account). Boundary (product-context.md,
# narrowed 2026-08-23): public listings only - NO login, NO credentials,
# NO auto-apply/messaging/connections, read-only, gently paced.
GUEST_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

PAGE_SIZE = 10
PAGE_DELAY_SECONDS = 1.5  # politeness between page requests


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


def _parse_cards(html: str) -> List[Dict[str, Any]]:
    from job_identity import generate_canonical_job_id, normalize_url

    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.base-search-card, div.base-card")
    now_iso = datetime.now(timezone.utc).isoformat()
    jobs = []
    for card in cards:
        try:
            title_el = card.select_one(".base-search-card__title")
            comp_el = card.select_one(".base-search-card__subtitle")
            link_el = card.select_one("a.base-card__full-link")
            if not title_el or not link_el:
                continue
            title = title_el.get_text(strip=True)
            link = (link_el.get("href") or "").split("?")[0]
            if not title or not link:
                continue

            loc_el = card.select_one(".job-search-card__location")
            date_el = card.select_one(".job-search-card__listdate, time")
            posted = (date_el.get("datetime") if date_el and date_el.get("datetime") else None) or now_iso
            company = comp_el.get_text(strip=True) if comp_el else "LinkedIn Listed Company"

            raw_job = {
                "title": title,
                "company": company,
                "location": loc_el.get_text(strip=True) if loc_el else "India",
                "url": link,
                "description": f"{title} at {company} (via LinkedIn public listing). See posting for details.",
                "posted_date": posted,
                "first_seen": now_iso,
                "source": "linkedin",
                "source_id": card.get("data-entity-urn") or "",
                "experience_required": "unspecified",
                "salary_range_inr": "unspecified",
                "parse_confidence": 0.85,
                "parser_method": "linkedin_guest_html",
            }
            job_id = generate_canonical_job_id(raw_job)
            raw_job["id"] = job_id
            raw_job["job_id"] = job_id
            raw_job["canonical_url"] = normalize_url(link)
            jobs.append(raw_job)
        except Exception:
            continue
    return jobs


def fetch_linkedin_jobs(
    role: str = "Software Engineer",
    location: str = "India",
    max_results: int = 30,
    return_metadata: bool = False,
):
    """Fetch public LinkedIn job listings (guest endpoint, no account)."""
    config = load_config()
    cfg = config.get("job_boards", {}).get("linkedin", {})

    if not cfg.get("enabled", True):
        health = {"status": "unconfigured", "message": "LinkedIn fetcher disabled in config", "http_status": None, "jobs_count": 0}
        print(f"[LinkedInFetcher] {health['message']}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    loc = location if (location or "").strip().lower() not in ("remote", "") else "India"
    params_base = {
        "keywords": role,
        "location": loc,
        "f_E": "1,2",   # internship + entry level
        "f_TPR": "r604800",  # posted within last 7 days
    }

    jobs: List[Dict[str, Any]] = []
    pages = 0
    try:
        for start in range(0, max_results, PAGE_SIZE):
            params = dict(params_base)
            params["start"] = start
            url = GUEST_URL + "?" + urllib.parse.urlencode(params)
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
            if resp.status_code == 429:
                print("[LinkedInFetcher] Rate limited (429) - stopping this cycle politely.")
                break
            if resp.status_code != 200 or not resp.text.strip():
                break
            page_jobs = _parse_cards(resp.text)
            if not page_jobs:
                break
            jobs.extend(page_jobs)
            pages += 1
            if len(jobs) >= max_results:
                jobs = jobs[:max_results]
                break
            time.sleep(PAGE_DELAY_SECONDS)

        h_status = "success" if jobs else "zero_results"
        health = {"status": h_status, "message": f"Fetched {len(jobs)} public listings across {pages} pages", "http_status": 200, "jobs_count": len(jobs)}
        print(f"[LinkedInFetcher] Successfully fetched {len(jobs)} public LinkedIn listings.")
        res = JobFetcherList(jobs, source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    except requests.exceptions.Timeout as e:
        health = {"status": "timeout", "message": str(e), "http_status": None, "jobs_count": 0}
        print(f"[LinkedInFetcher] Timeout: {e}")
        res = JobFetcherList(jobs, source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res
    except Exception as e:
        health = {"status": "unavailable", "message": str(e), "http_status": None, "jobs_count": 0}
        print(f"[LinkedInFetcher] Error: {e}")
        res = JobFetcherList(jobs, source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res
