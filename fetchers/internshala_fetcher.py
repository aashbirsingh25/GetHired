import os
import json
from datetime import datetime, timezone
from typing import List, Dict, Any

import requests
from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# Internshala serves listing cards server-side (verified 2026-08-23:
# HTTP 200, ~40 cards/page, no bot wall). India's main internship/fresher
# board - core source for a 0-experience candidate.
BASE_URL = "https://internshala.com"
CATEGORY_PATH = "/internships/computer-science-internship/"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

LOCATION_SLUGS = {
    "gurugram": "computer-science-internship-in-gurgaon",
    "gurgaon": "computer-science-internship-in-gurgaon",
    "bangalore": "computer-science-internship-in-bangalore",
    "bengaluru": "computer-science-internship-in-bangalore",
    "delhi": "computer-science-internship-in-delhi",
    "noida": "computer-science-internship-in-noida",
    "remote": "work-from-home-computer-science-internships",
}


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


def _parse_cards(html: str, max_results: int) -> List[Dict[str, Any]]:
    from job_identity import generate_canonical_job_id, normalize_url

    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.individual_internship")
    now_iso = datetime.now(timezone.utc).isoformat()
    jobs = []
    for card in cards:
        if len(jobs) >= max_results:
            break
        try:
            title_el = card.select_one("a.job-title-href") or card.select_one("h2 a")
            comp_el = card.select_one("p.company-name")
            if not title_el or not comp_el:
                continue
            title = title_el.get_text(strip=True)
            href = card.get("data-href") or title_el.get("href") or ""
            if not title or not href:
                continue
            link = BASE_URL + href if href.startswith("/") else href

            loc_el = card.select_one(".locations span")
            location = loc_el.get_text(" ", strip=True) if loc_el else "India"
            stipend_el = card.select_one("span.stipend")
            stipend = stipend_el.get_text(strip=True) if stipend_el else "unspecified"

            raw_job = {
                "title": f"{title} Intern" if "intern" not in title.lower() else title,
                "company": comp_el.get_text(strip=True),
                "location": location,
                "url": link,
                "description": f"{title} internship at {comp_el.get_text(strip=True)}. Stipend: {stipend}. 0 years experience - internship role.",
                "posted_date": now_iso,
                "first_seen": now_iso,
                "source": "internshala",
                "source_id": card.get("internshipid") or card.get("id") or "",
                "experience_required": "0 years",
                "salary_range_inr": stipend,
                "parse_confidence": 0.90,
                "parser_method": "internshala_html",
            }
            job_id = generate_canonical_job_id(raw_job)
            raw_job["id"] = job_id
            raw_job["job_id"] = job_id
            raw_job["canonical_url"] = normalize_url(link)
            jobs.append(raw_job)
        except Exception:
            continue
    return jobs


def fetch_internshala_jobs(
    role: str = "Software Engineer",
    location: str = "Remote",
    max_results: int = 40,
    return_metadata: bool = False,
):
    config = load_config()
    cfg = config.get("job_boards", {}).get("internshala", {})

    if not cfg.get("enabled", True):
        health = {"status": "unconfigured", "message": "Internshala fetcher disabled in config", "http_status": None, "jobs_count": 0}
        print(f"[InternshalaFetcher] {health['message']}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    slug = LOCATION_SLUGS.get((location or "").strip().lower())
    urls = [f"{BASE_URL}/internships/{slug}/" if slug else f"{BASE_URL}{CATEGORY_PATH}"]
    if slug:
        urls.append(f"{BASE_URL}{CATEGORY_PATH}")  # fallback to all-category page

    try:
        jobs = []
        used_url = urls[0]
        for url in urls:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
            if resp.status_code != 200:
                continue
            jobs = _parse_cards(resp.text, max_results)
            if jobs:
                used_url = url
                break

        h_status = "success" if jobs else "zero_results"
        health = {"status": h_status, "message": f"Fetched {len(jobs)} internships from Internshala ({used_url})", "http_status": 200, "jobs_count": len(jobs)}
        print(f"[InternshalaFetcher] Successfully fetched {len(jobs)} internships.")
        res = JobFetcherList(jobs, source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    except requests.exceptions.Timeout as e:
        health = {"status": "timeout", "message": str(e), "http_status": None, "jobs_count": 0}
        print(f"[InternshalaFetcher] Timeout: {e}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res
    except Exception as e:
        health = {"status": "unavailable", "message": str(e), "http_status": None, "jobs_count": 0}
        print(f"[InternshalaFetcher] Error: {e}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res
