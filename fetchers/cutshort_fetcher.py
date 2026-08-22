import os
import json
import re
from datetime import datetime, timezone
from typing import List, Dict, Any

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# Cutshort renders job lists server-side (Next.js) and embeds the data as
# JSON in a <script id="__NEXT_DATA__"> tag. No API key or captcha needed
# (verified 2026-08-23: plain GET returns HTTP 200 with 50 jobs per page).
CATEGORY_SLUG = "software-development-jobs"

# Cutshort's location slugs for the user's target cities.
LOCATION_SLUGS = {
    "gurugram": "gurugram-gurgaon",
    "gurgaon": "gurugram-gurgaon",
    "bangalore": "bangalore-bengaluru",
    "bengaluru": "bangalore-bengaluru",
    "delhi": "delhi-ncr",
    "noida": "noida",
    "remote": "remote",
}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
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


def _extract_next_data(html: str) -> Dict[str, Any]:
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.S,
    )
    if not m:
        raise ValueError("__NEXT_DATA__ script tag not found (page layout may have changed)")
    return json.loads(m.group(1))


def _jobs_from_next_data(next_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Note: use `or {}` (not .get defaults) — keys can exist with None values.
    props = next_data.get("props") or {}
    page_props = props.get("pageProps") or {}
    dehydrated = page_props.get("dehydratedState") or {}
    queries = dehydrated.get("queries") or []
    for q in queries:
        if not isinstance(q, dict):
            continue
        key = q.get("queryKey")
        if isinstance(key, list) and key and key[0] == "jobListData":
            state = q.get("state") or {}
            data = state.get("data") or {}
            inner = data.get("data") or {}
            page_data = inner.get("pageData") or {}
            jobs = page_data.get("jobs")
            if isinstance(jobs, list):
                return jobs
    return []


def fetch_cutshort_jobs(
    role: str = "Software Engineer",
    location: str = "Gurugram",
    max_results: int = 25,
    return_metadata: bool = False,
):
    """Fetch software jobs from Cutshort's public listing pages."""
    config = load_config()
    cutshort_cfg = config.get("job_boards", {}).get("cutshort", {})

    if not cutshort_cfg.get("enabled", True):
        health = {
            "status": "unconfigured",
            "message": "Cutshort fetcher disabled in config",
            "http_status": None,
            "jobs_count": 0,
        }
        print(f"[CutshortFetcher] {health['message']}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    loc_slug = LOCATION_SLUGS.get((location or "").strip().lower())
    url = f"https://cutshort.io/jobs/{CATEGORY_SLUG}"
    if loc_slug:
        url = f"https://cutshort.io/jobs/{CATEGORY_SLUG}-in-{loc_slug}"

    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        if resp.status_code != 200:
            status = "blocked" if resp.status_code in (403, 406) else "unavailable"
            health = {
                "status": status,
                "message": f"Cutshort returned HTTP {resp.status_code}",
                "http_status": resp.status_code,
                "jobs_count": 0,
            }
            print(f"[CutshortFetcher] {health['message']}")
            res = JobFetcherList([], source_health=health)
            return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

        raw_jobs = _jobs_from_next_data(_extract_next_data(resp.text))

        # Some location pages render their list client-side (no embedded
        # jobs). Fall back to the unfiltered category page in that case.
        if not raw_jobs and loc_slug:
            fallback_url = f"https://cutshort.io/jobs/{CATEGORY_SLUG}"
            print(f"[CutshortFetcher] No embedded jobs at {url}; falling back to {fallback_url}")
            resp = requests.get(fallback_url, headers={"User-Agent": USER_AGENT}, timeout=20)
            if resp.status_code == 200:
                raw_jobs = _jobs_from_next_data(_extract_next_data(resp.text))
                url = fallback_url

        from job_identity import generate_canonical_job_id, normalize_url

        now_iso = datetime.now(timezone.utc).isoformat()
        jobs = []
        for idx, rj in enumerate(raw_jobs[:max_results]):
            try:
                title = (rj.get("headline") or "").strip()
                link = (rj.get("publicUrl") or "").strip()
                if not title or not link:
                    continue

                company = ((rj.get("companyDetails") or {}).get("name") or "Cutshort Listed Company").strip()
                locations = rj.get("locations") or []
                remote_type = (rj.get("remoteType") or "").strip()
                job_location = ", ".join(locations) if locations else (remote_type or location)

                skills = rj.get("allSkills") or []
                exp_range = rj.get("expRange") or {}
                exp_text = ""
                if isinstance(exp_range, dict) and exp_range.get("min") is not None:
                    exp_text = f"{exp_range.get('min')}-{exp_range.get('max')} years"

                description = ((rj.get("companyDetails") or {}).get("sanitizedDescription") or "").strip()
                if skills:
                    description = (description + " Skills: " + ", ".join(str(s) for s in skills)).strip()
                if not description:
                    description = f"{title} opportunity at {company}."

                raw_job = {
                    "title": title,
                    "company": company,
                    "location": job_location,
                    "url": link,
                    "description": description,
                    "posted_date": now_iso,
                    "first_seen": now_iso,
                    "source": "cutshort",
                    "source_id": rj.get("_id"),
                    "experience_required": exp_text or "unspecified",
                    "salary_range_inr": (rj.get("salaryRangeText") or "").strip() or "unspecified",
                    "parse_confidence": 0.90,
                    "parser_method": "cutshort_next_data",
                }
                job_id = generate_canonical_job_id(raw_job)
                raw_job["id"] = job_id
                raw_job["job_id"] = job_id
                raw_job["canonical_url"] = normalize_url(link)
                jobs.append(raw_job)
            except Exception as entry_err:
                print(f"[CutshortFetcher] Warning: failed parsing entry #{idx}: {entry_err}")
                continue

        h_status = "success" if jobs else "zero_results"
        health = {
            "status": h_status,
            "message": f"Fetched {len(jobs)} jobs from Cutshort ({url})",
            "http_status": 200,
            "jobs_count": len(jobs),
        }
        print(f"[CutshortFetcher] Successfully fetched {len(jobs)} jobs from Cutshort.")
        res = JobFetcherList(jobs, source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    except requests.exceptions.Timeout as e:
        health = {"status": "timeout", "message": str(e), "http_status": None, "jobs_count": 0}
        print(f"[CutshortFetcher] Timeout accessing Cutshort ({url}): {e}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res
    except Exception as e:
        health = {"status": "unavailable", "message": str(e), "http_status": None, "jobs_count": 0}
        print(f"[CutshortFetcher] Error accessing Cutshort ({url}): {e}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res
