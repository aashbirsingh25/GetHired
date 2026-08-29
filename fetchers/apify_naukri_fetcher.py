"""Apify-backed Naukri fetcher: fresher jobs, all India, last 24 hours.

Uses the muhammetakkurtt/naukri-job-scraper actor (pay-per-event:
$0.01/start + $0.005/job on the FREE tier; the actor enforces maxJobs >= 50,
so a full run costs about $0.26 and typically returns 15-50 items).

Budget: the free Apify plan grants $5/month. A hard monthly cap (default
$4.50) and a minimum gap between runs (default 20h) are enforced HERE, in
code, because the account gets suspended if credits run out mid-run.
Spend is tracked in apify_usage.json.

Verified live 2026-08-29: 15 fresher jobs posted within 24h, real posted
labels ("Just Now", "Few Hours Ago"), full descriptions and skill tags.
"""
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USAGE_FILE = os.path.join(BASE_DIR, "apify_usage.json")

ACTOR = "muhammetakkurtt~naukri-job-scraper"
RUN_START_USD = 0.01
PER_JOB_USD = 0.005
MONTHLY_CAP_USD = 4.50
MIN_HOURS_BETWEEN_RUNS = 20


class JobFetcherList(list):
    """List with attached source health metadata (same contract as the
    other fetchers in this package)."""
    def __init__(self, items=(), source_health=None):
        super().__init__(items)
        self.source_health = source_health or {
            "status": "success" if items else "zero_results",
            "message": f"Returned {len(items)} jobs",
            "http_status": 200,
            "jobs_count": len(items),
        }


def _load_usage() -> Dict[str, Any]:
    try:
        return json.load(open(USAGE_FILE, encoding="utf-8"))
    except Exception:
        return {"month": "", "spent_usd": 0.0, "runs": [], "last_run_at": None}


def _save_usage(u: Dict[str, Any]) -> None:
    tmp = USAGE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(u, f, indent=1)
    os.replace(tmp, USAGE_FILE)


def _budget_gate() -> str:
    """Empty string when a run is allowed; otherwise the reason it is not."""
    u = _load_usage()
    month = datetime.now().strftime("%Y-%m")
    if u.get("month") != month:
        return ""  # new month, fresh budget
    projected = (u.get("spent_usd") or 0.0) + RUN_START_USD + 50 * PER_JOB_USD
    if projected > MONTHLY_CAP_USD:
        return (f"monthly Apify budget guard: ${u.get('spent_usd', 0):.2f} spent, "
                f"a worst-case run (${RUN_START_USD + 50 * PER_JOB_USD:.2f}) would exceed "
                f"the ${MONTHLY_CAP_USD:.2f} cap")
    last = u.get("last_run_at")
    if last:
        try:
            hours = (datetime.now(timezone.utc)
                     - datetime.fromisoformat(last)).total_seconds() / 3600.0
            if hours < MIN_HOURS_BETWEEN_RUNS:
                return f"ran {hours:.1f}h ago; minimum gap is {MIN_HOURS_BETWEEN_RUNS}h"
        except Exception:
            pass
    return ""


def _record_run(n_items: int) -> None:
    u = _load_usage()
    month = datetime.now().strftime("%Y-%m")
    if u.get("month") != month:
        u = {"month": month, "spent_usd": 0.0, "runs": [], "last_run_at": None}
    cost = RUN_START_USD + n_items * PER_JOB_USD
    u["spent_usd"] = round((u.get("spent_usd") or 0.0) + cost, 4)
    u["last_run_at"] = datetime.now(timezone.utc).isoformat()
    u.setdefault("runs", []).append(
        {"at": u["last_run_at"], "items": n_items, "cost_usd": round(cost, 4)})
    u["runs"] = u["runs"][-60:]
    _save_usage(u)


def fetch_apify_naukri_jobs(role: str = "software engineer",
                            max_results: int = 50,
                            return_metadata: bool = False):
    """Fetch fresher (0 yrs) India jobs posted in the last 24h from Naukri."""
    token = (os.environ.get("APIFY_API_TOKEN") or "").strip()
    if not token:
        health = {"status": "unconfigured", "message": "APIFY_API_TOKEN not set",
                  "http_status": None, "jobs_count": 0}
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    reason = _budget_gate()
    if reason:
        health = {"status": "budget_deferred", "message": reason,
                  "http_status": None, "jobs_count": 0}
        print(f"[ApifyNaukri] Skipping run: {reason}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    payload = {
        "keyword": role,
        "maxJobs": max(50, min(max_results, 50)),  # actor minimum is 50
        "freshness": "1",       # posted within 24h - the whole point
        "experience": "0",      # strictly fresher
        "sortBy": "date",
        "fetchDetails": False,  # description already included; details double cost
    }
    url = (f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"
           f"?token={token}&timeout=240")
    try:
        r = requests.post(url, json=payload, timeout=280)
    except Exception as e:
        health = {"status": "unavailable", "message": f"Apify request failed: {e}",
                  "http_status": None, "jobs_count": 0}
        print(f"[ApifyNaukri] {health['message']}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    if r.status_code not in (200, 201):
        health = {"status": "unavailable",
                  "message": f"Apify returned HTTP {r.status_code}: {r.text[:120]}",
                  "http_status": r.status_code, "jobs_count": 0}
        print(f"[ApifyNaukri] {health['message']}")
        res = JobFetcherList([], source_health=health)
        return {"status": health["status"], "health": health, "jobs": res} if return_metadata else res

    try:
        items = r.json()
        assert isinstance(items, list)
    except Exception:
        items = []
    _record_run(len(items))

    now_iso = datetime.now(timezone.utc).isoformat()
    jobs: List[Dict[str, Any]] = []
    for it in items:
        title = (it.get("title") or "").strip()
        link = it.get("jdURL") or ""
        if not title or not link:
            continue
        skills = it.get("tagsAndSkills") or ""
        desc = (it.get("jobDescription") or "").strip()
        if skills:
            desc = f"{desc}\n\nSkills: {skills}".strip()
        exp = it.get("experienceText") or "0 years"
        desc = f"{desc}\n\nExperience: {exp}".strip()
        # createdDate is the REAL posting time. Observed live as a naive
        # "YYYY-MM-DD HH:MM:SS" string (IST); epoch millis handled defensively.
        posted = None
        cd = it.get("createdDate")
        if isinstance(cd, (int, float)) and cd > 1e12:
            posted = datetime.fromtimestamp(cd / 1000.0, tz=timezone.utc).isoformat()
        elif isinstance(cd, str) and cd:
            try:
                posted = datetime.strptime(cd, "%Y-%m-%d %H:%M:%S").isoformat()
            except ValueError:
                pass
        jobs.append({
            "id": f"apify-naukri-{it.get('jobId')}",
            "title": title,
            "company": (it.get("companyName") or "Unknown").strip(),
            "location": (it.get("placeholders") or {}).get("location")
                        if isinstance(it.get("placeholders"), dict)
                        else (it.get("location") or "India"),
            "url": link,
            "description": desc[:3000],
            "posted_date": posted,
            "posted_label": it.get("footerPlaceholderLabel"),
            "source": "naukri_apify",
            "extraction_method": "apify_actor",
            "scan_timestamp": now_iso,
            "first_seen_at": now_iso,
            "closed": False,
            "needs_manual_link_review": False,
            "match": None,
        })

    print(f"[ApifyNaukri] {len(jobs)} fresher (24h) jobs; "
          f"run cost ${RUN_START_USD + len(items) * PER_JOB_USD:.2f}")
    res = JobFetcherList(jobs)
    return {"status": "success", "health": res.source_health, "jobs": res} if return_metadata else res
