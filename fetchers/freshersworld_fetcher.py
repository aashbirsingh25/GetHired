import os
import re
import json
import time
from datetime import datetime, timezone
from typing import List, Dict, Any

import requests
from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# Freshersworld: an Indian board built specifically for freshers/graduates.
# Listings are plain server-rendered HTML (20 job containers per page), so no
# key or browser is needed. Verified 2026-08-24 on /jobs/jobsearch/*.
BASE = "https://www.freshersworld.com"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
PAGE_DELAY_SECONDS = 1.5

SEARCH_PATHS = [
    "/jobs/jobsearch/software-jobs",
    "/jobs/jobsearch/software-engineer-jobs",
    "/jobs/jobsearch/it-software-jobs",
    "/jobs/jobsearch/fresher-software-developer-jobs",
    "/jobs/jobsearch/software-jobs-in-bangalore",
    "/jobs/jobsearch/software-jobs-in-gurgaon",
    "/jobs/jobsearch/software-jobs-in-noida",
    "/jobs/jobsearch/software-jobs-in-hyderabad",
    "/jobs/jobsearch/internship-jobs",
    "/jobs/jobsearch/computer-science-jobs",
]

_YEARS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|to)?\s*(\d+(?:\.\d+)?)?\s*Years?", re.I)


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
    cards = soup.select("div.job-container")
    now_iso = datetime.now(timezone.utc).isoformat()
    jobs: List[Dict[str, Any]] = []

    for card in cards:
        if len(jobs) >= max_results:
            break
        try:
            title_el = card.select_one(".job-new-title, h3, h2")
            link_el = card.select_one("a[href]")
            if not title_el or not link_el:
                continue
            raw_title = title_el.get_text(" ", strip=True)
            if not raw_title:
                continue

            href = link_el.get("href") or ""
            url = BASE + href if href.startswith("/") else href

            # Card text carries company / location / experience / qualification
            parts = [t.strip() for t in card.stripped_strings if t.strip()]
            blob = " | ".join(parts)

            # Title format: "<Role> Jobs Opening in <Company> at <Location>".
            # The title element also picks up the card's "Less/More" toggle
            # text, so trim anything from those tokens onward.
            m = re.match(r"(.*?)\s+Jobs?\s+Opening\s+in\s+(.*?)\s+at\s+(.*)$", raw_title, re.I)
            if m:
                title, company, location = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
                location = re.split(r"\s+(?:Less|More)\b", location)[0].strip(" |,")
            else:
                title, company, location = raw_title, "Freshersworld Listed Company", "India"
            # Many Freshersworld posts are agency-anonymised; keep the label but
            # make it explicit rather than looking like a real employer name.
            if company.lower().startswith("a client of"):
                company = "Freshersworld (undisclosed employer)"

            exp = "unspecified"
            ym = _YEARS_RE.search(blob)
            if ym:
                exp = ym.group(0).strip()

            salary = "unspecified"
            if "Salary not disclosed" not in blob:
                sm = re.search(r"(₹|Rs\.?)\s?[\d,.]+\s?(?:-|to)?\s?[\d,.]*\s?(?:LPA|Lakh|/month|per month)?", blob, re.I)
                if sm:
                    salary = sm.group(0).strip()

            job = {
                "title": title,
                "company": company,
                "location": location or "India",
                "url": url,
                "description": f"{title} at {company}, {location}. Experience: {exp}. (Freshersworld listing)",
                "posted_date": now_iso,
                "first_seen": now_iso,
                "source": "freshersworld",
                "source_id": href.rstrip("/").split("/")[-1] if href else "",
                "experience_required": exp,
                "salary_range_inr": salary,
                "parse_confidence": 0.85,
                "parser_method": "freshersworld_html",
            }
            jid = generate_canonical_job_id(job)
            job["id"] = jid
            job["job_id"] = jid
            job["canonical_url"] = normalize_url(url)
            jobs.append(job)
        except Exception:
            continue
    return jobs


def fetch_freshersworld_jobs(role: str = "Software Engineer", location: str = "India",
                             max_results: int = 60, paths_per_cycle: int = 3,
                             cycle_seed: int = 0, return_metadata: bool = False):
    """Fetch fresher-focused listings from Freshersworld (rotating path slice)."""
    config = load_config()
    cfg = config.get("job_boards", {}).get("freshersworld", {})
    if not cfg.get("enabled", True):
        health = {"status": "unconfigured", "message": "Freshersworld fetcher disabled in config",
                  "http_status": None, "jobs_count": 0}
        print(f"[FreshersworldFetcher] {health['message']}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    n = max(1, paths_per_cycle)
    start = (cycle_seed * n) % len(SEARCH_PATHS)
    picked = [SEARCH_PATHS[(start + i) % len(SEARCH_PATHS)] for i in range(n)]

    seen, jobs = set(), []
    last_status = None
    try:
        for path in picked:
            if len(jobs) >= max_results:
                break
            try:
                r = requests.get(BASE + path, headers={"User-Agent": USER_AGENT}, timeout=25)
                last_status = r.status_code
                if r.status_code != 200:
                    print(f"[FreshersworldFetcher] {path} -> HTTP {r.status_code}")
                    continue
                for job in _parse_cards(r.text, max_results - len(jobs)):
                    if job["id"] not in seen:
                        seen.add(job["id"])
                        jobs.append(job)
            except Exception as e:
                print(f"[FreshersworldFetcher] {path} failed: {str(e)[:80]}")
            time.sleep(PAGE_DELAY_SECONDS)

        status = "success" if jobs else ("blocked" if last_status in (403, 429) else "zero_results")
        health = {"status": status,
                  "message": f"Fetched {len(jobs)} jobs from {len(picked)} Freshersworld searches",
                  "http_status": last_status or 200, "jobs_count": len(jobs)}
        print(f"[FreshersworldFetcher] Successfully fetched {len(jobs)} jobs "
              f"(paths {start}-{start + n - 1} of {len(SEARCH_PATHS)}).")
        res = JobFetcherList(jobs, source_health=health)
        return {"status": status, "health": health, "jobs": res} if return_metadata else res
    except Exception as e:
        health = {"status": "unavailable", "message": str(e), "http_status": None, "jobs_count": 0}
        print(f"[FreshersworldFetcher] Error: {e}")
        res = JobFetcherList(jobs, source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res
