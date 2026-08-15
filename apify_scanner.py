import os
import json
import time
import requests
import hashlib
import re
import calendar
from datetime import datetime
from typing import List, Tuple, Dict, Any
from store_integrity_checker import check_job_posting_validity

BASE_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
USAGE_LOG_FILE = os.path.join(BASE_DIR, "apify_usage_log.json")

class ApifyUnavailableError(Exception):
    """Raised when Apify API is disabled, unconfigured, out of quota, or encounters a runtime failure."""
    pass

def load_json(filepath, default):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

import time
import threading

def save_json(filepath, data, indent=2):
    dir_name = os.path.dirname(filepath) or "."
    os.makedirs(dir_name, exist_ok=True)
    tmp_path = f"{filepath}.tmp_{os.getpid()}_{threading.get_ident()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp_path, filepath)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02)
    except Exception as e:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        raise e

def log_apify_usage(company_name: str, credits_used: float, duration_s: float):
    now = datetime.now()
    now_iso = now.isoformat()
    curr_month = now.strftime("%Y-%m")

    log_data = load_json(USAGE_LOG_FILE, {"calls": []})
    calls = log_data.get("calls", [])

    # Compute running monthly total for current month
    running_monthly_total = 0.0
    for call in calls:
        ts = call.get("timestamp", "")
        if ts.startswith(curr_month):
            running_monthly_total += call.get("credits_used", 0.0)

    running_monthly_total += credits_used

    entry = {
        "timestamp": now_iso,
        "company": company_name,
        "credits_used": round(credits_used, 4),
        "duration_s": round(duration_s, 2),
        "running_monthly_total": round(running_monthly_total, 4)
    }

    calls.append(entry)
    log_data["calls"] = calls
    save_json(USAGE_LOG_FILE, log_data)
    return entry

def compute_apify_pacing() -> Tuple[float, float]:
    """
    Computes pace_ratio = actual_credits_used_this_month / expected_credits_used_by_now.
    Returns (pace_ratio, modifier) where modifier is +0.03 if underused (<0.7), -0.03 if burning fast (>1.2), else 0.0.
    Bounded so pacing modifier alone never exceeds +/-0.05.
    """
    cfg = load_json(CONFIG_FILE, {})
    apify_cfg = cfg.get("apify", {})
    monthly_limit = float(apify_cfg.get("monthly_credit_limit_usd", 5.0))

    now = datetime.now()
    curr_month = now.strftime("%Y-%m")
    _, days_in_month = calendar.monthrange(now.year, now.month)
    days_elapsed = max(1, now.day)

    expected_credits = (days_elapsed / float(days_in_month)) * monthly_limit

    log_data = load_json(USAGE_LOG_FILE, {"calls": []})
    calls = log_data.get("calls", [])

    actual_credits = 0.0
    for call in calls:
        ts = call.get("timestamp", "")
        if ts.startswith(curr_month):
            actual_credits += call.get("credits_used", 0.0)

    if expected_credits <= 0:
        pace_ratio = 1.0
    else:
        pace_ratio = round(actual_credits / expected_credits, 2)

    if pace_ratio < 0.7:
        modifier = 0.03  # +3 points threshold boost for extra verification
    elif pace_ratio > 1.2:
        modifier = -0.03  # -3 points threshold lower to conserve quota
    else:
        modifier = 0.0

    # Ensure modifier is bounded to max +/-0.05
    modifier = max(-0.05, min(0.05, modifier))
    return pace_ratio, modifier

def get_avg_apify_duration() -> float:
    """Returns rolling average call duration from apify_usage_log.json in seconds (default 30.0s)."""
    log_data = load_json(USAGE_LOG_FILE, {"calls": []})
    calls = log_data.get("calls", [])
    if not calls:
        return 30.0
    durations = [c.get("duration_s", 30.0) for c in calls if c.get("duration_s")]
    if not durations:
        return 30.0
    return round(sum(durations) / len(durations), 2)

def load_dotenv(dotenv_path=None):
    if dotenv_path is None:
        dotenv_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(dotenv_path):
        try:
            with open(dotenv_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'").strip('"')
                    if k and k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass

class ApifyScanner:
    def __init__(self):
        load_dotenv()
        cfg = load_json(CONFIG_FILE, {})
        apify_cfg = cfg.get("apify", {})
        self.enabled = apify_cfg.get("enabled", True)
        raw_token = os.environ.get("APIFY_API_TOKEN") or os.environ.get("APIFY_TOKEN") or apify_cfg.get("api_token", "")
        if raw_token.startswith("YOUR_"):
            raw_token = ""
        self.api_token = raw_token.strip()
        self.monthly_credit_limit_usd = float(apify_cfg.get("monthly_credit_limit_usd", 5.0))
        self.daily_hard_cap_usd = float(apify_cfg.get("daily_hard_cap_usd", 1.50))
        self.actor_id = apify_cfg.get("actor_id", "apify/website-content-crawler").strip()

    def scan_company_via_apify(self, company: dict) -> Tuple[List[dict], float, str]:
        """
        Scans company career page using general web scraping actor via Apify REST API.
        Explicitly avoids any LinkedIn actors.
        Returns: (jobs_list, confidence_score, "apify")
        Raises: ApifyUnavailableError if unconfigured, out of quota, or network error.
        """
        # Quota & Daily Hard Cap check
        pace_ratio, _ = compute_apify_pacing()
        log_data = load_json(USAGE_LOG_FILE, {"calls": []})
        now = datetime.now()
        curr_month = now.strftime("%Y-%m")
        today_date = now.strftime("%Y-%m-%d")

        daily_credits = sum(c.get("credits_used", 0.0) for c in log_data.get("calls", []) if c.get("timestamp", "").startswith(today_date))
        if daily_credits >= self.daily_hard_cap_usd:
            print(f"Daily Apify spending cap reached (${self.daily_hard_cap_usd:.2f}) - disabled until tomorrow (${daily_credits:.2f} used)")
            raise ApifyUnavailableError(f"Daily Apify spending cap reached (${self.daily_hard_cap_usd:.2f}) - disabled until tomorrow")

        if not self.enabled:
            raise ApifyUnavailableError("Apify scanning fallback is disabled in config.json")
        if not self.api_token:
            raise ApifyUnavailableError("Apify API token is empty or unconfigured")

        month_credits = sum(c.get("credits_used", 0.0) for c in log_data.get("calls", []) if c.get("timestamp", "").startswith(curr_month))
        if month_credits >= self.monthly_credit_limit_usd:
            raise ApifyUnavailableError(f"Monthly credit limit of ${self.monthly_credit_limit_usd:.2f} reached (${month_credits:.2f} used)")


        company_name = company.get("name", "Unknown Company")
        company_id = company.get("id", "comp")
        career_url = company.get("career_url", "")

        if not career_url:
            raise ApifyUnavailableError(f"Company {company_name} has no career URL")

        start_time = time.time()
        clean_actor_id = self.actor_id.replace("/", "~")
        api_url = f"https://api.apify.com/v2/acts/{clean_actor_id}/run-sync-get-dataset-items?token={self.api_token}"

        payload = {
            "startUrls": [{"url": career_url}],
            "maxCrawlPages": 2,
            "crawlerType": "cheerio"
        }

        try:
            resp = requests.post(api_url, json=payload, timeout=25)
            duration_s = time.time() - start_time
            credits_estimated = 0.02  # ~$0.02 nominal credit cost per sync scrape call

            if resp.status_code != 200 and resp.status_code != 201:
                log_apify_usage(company_name, 0.0, duration_s)
                raise ApifyUnavailableError(f"Apify API returned HTTP status {resp.status_code}: {resp.text[:200]}")

            items = resp.json()
            if not isinstance(items, list):
                items = items.get("items", []) if isinstance(items, dict) else []

            jobs = []
            now_iso = datetime.now().isoformat()

            for item in items:
                title = item.get("title") or item.get("jobTitle") or item.get("header")
                if not title or len(str(title).strip()) < 3:
                    continue

                title = str(title).strip()
                loc = item.get("location") or item.get("jobLocation") or "India"
                url = item.get("url") or item.get("link") or career_url
                desc = item.get("description") or item.get("text") or title

                from job_identity import generate_canonical_job_id, normalize_url
                cand_job = {
                    "company": company_name,
                    "title": title,
                    "location": str(loc).strip(),
                    "url": str(url).strip(),
                    "description": str(desc)[:500],
                    "posted_date": item.get("postedDate"),
                    "source": "apify",
                    "sources": ["apify"],
                    "extraction_method": "apify",
                    "scan_timestamp": now_iso,
                    "first_seen": now_iso,
                    "first_seen_at": now_iso,
                    "closed": False,
                    "match": None
                }
                job_id = generate_canonical_job_id(cand_job)
                cand_job["id"] = job_id
                cand_job["job_id"] = job_id
                cand_job["canonical_url"] = normalize_url(str(url).strip())
                is_valid, _ = check_job_posting_validity(cand_job)
                if is_valid:
                    jobs.append(cand_job)

            log_apify_usage(company_name, credits_estimated, duration_s)
            return jobs, 0.90, "apify"

        except requests.RequestException as re_err:
            duration_s = time.time() - start_time
            log_apify_usage(company_name, 0.0, duration_s)
            raise ApifyUnavailableError(f"Apify connection error: {str(re_err)}")
        except Exception as e:
            duration_s = time.time() - start_time
            log_apify_usage(company_name, 0.0, duration_s)
            raise ApifyUnavailableError(f"Apify processing error: {str(e)}")
