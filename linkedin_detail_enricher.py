"""Enrich stub LinkedIn jobs with real descriptions from their public pages.

WHY: the LinkedIn sweep captures title/company/location/link only. The stored
description is a one-line stub, so requirement text like "3+ years" never
reaches the experience filter - that is exactly how a MongoDB 'Software
Engineer 3' (3+ yrs in its posting) reached a strictly-fresher feed.

Feasibility verified live 2026-09-05: logged-out fetch of a job /view/ page
returned HTTP 200 with the full description in show-more-less-html__markup
(no authwall). Some page variants omit it; those are marked and skipped.

DELIBERATELY GENTLE: LinkedIn walls aggressive scrapers, and the daily sweep
matters more than enrichment. One fetch per ENRICH_INTERVAL_S, at most
DAILY_CAP per day, feed-relevant jobs only (score >= 50), permanent-failure mark
so nothing is retried forever. Any authwall/999 response stops the whole day.
"""
import json
import os
import re
import threading
import time
import urllib.request
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_FILE = os.path.join(BASE_DIR, "jobs_store.json")
STATE_FILE = os.path.join(BASE_DIR, "linkedin_enrich_state.json")

ENRICH_INTERVAL_S = 20          # one page fetch per 20s - slower than any human
DAILY_CAP = 60                  # at most 60 detail pages per day
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

_DESC_RE = re.compile(r'<div class="show-more-less-html__markup[^"]*">(.*?)</div>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _load_state():
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception:
        return {"date": "", "fetched_today": 0, "done_ids": [], "failed_ids": [], "walled_until": None}


def _save_state(st):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATE_FILE)


def _fetch_description(url):
    """Returns (description_text or None, walled: bool)."""
    req = urllib.request.Request(url, headers=UA)
    r = urllib.request.urlopen(req, timeout=20)
    if "authwall" in r.url or "signup" in r.url:
        return None, True
    if r.status != 200:
        return None, r.status == 999
    html = r.read().decode("utf-8", errors="ignore")
    m = _DESC_RE.search(html)
    if not m:
        return None, False
    text = _TAG_RE.sub(" ", m.group(1))
    text = re.sub(r"\s+", " ", text).strip()
    return (text if len(text) > 100 else None), False


def _merge_save_description(job_id, desc):
    """Merge one enriched description into the live store (merge-safe)."""
    from scan_coordinator import save_json  # atomic tmp+rename writer
    data = json.load(open(JOBS_FILE, encoding="utf-8"))
    for j in data.get("jobs", []):
        if j.get("id") == job_id:
            j["description"] = desc[:6000]
            j["description_enriched_at"] = datetime.now().isoformat()
            # the old match scored a stub; clear tier-5 matches so the
            # rescorer re-evaluates with real content (never touch tier 1/2)
            m = j.get("match")
            if isinstance(m, dict) and m.get("tier") not in (1, 2):
                j["match"] = None
            break
    save_json(JOBS_FILE, data)


def enrichment_loop():
    """Background thread: forever, gently enrich stub LinkedIn feed jobs."""
    time.sleep(600)  # let startup scan settle
    while True:
        try:
            st = _load_state()
            today = datetime.now().strftime("%Y-%m-%d")
            if st.get("date") != today:
                st.update(date=today, fetched_today=0, walled_until=None)
            if st.get("walled_until") == today:
                time.sleep(3600)
                continue
            if st["fetched_today"] >= DAILY_CAP:
                time.sleep(3600)
                continue

            jobs = json.load(open(JOBS_FILE, encoding="utf-8")).get("jobs", [])
            done = set(st.get("done_ids", [])) | set(st.get("failed_ids", []))
            cands = [j for j in jobs
                     if j.get("source") == "linkedin"
                     and "linkedin.com/jobs/view" in (j.get("url") or "")
                     and j.get("id") not in done
                     and len(j.get("description") or "") < 140
                     and ((j.get("match") or {}).get("score") or 0) >= 50]
            cands.sort(key=lambda j: -((j.get("match") or {}).get("score") or 0))
            if not cands:
                time.sleep(1800)
                continue

            job = cands[0]
            try:
                desc, walled = _fetch_description(job["url"])
            except Exception as e:
                print(f"[LinkedInEnrich] fetch error for {job['id']}: {str(e)[:80]}")
                desc, walled = None, False
            if walled:
                print("[LinkedInEnrich] Hit authwall/999 - stopping for the day (protecting the sweep).")
                st["walled_until"] = today
                _save_state(st)
                continue
            st["fetched_today"] += 1
            if desc:
                _merge_save_description(job["id"], desc)
                st.setdefault("done_ids", []).append(job["id"])
                print(f"[LinkedInEnrich] Enriched '{job.get('title','')[:40]}' "
                      f"({len(desc)} chars) [{st['fetched_today']}/{DAILY_CAP} today]")
            else:
                st.setdefault("failed_ids", []).append(job["id"])
            st["done_ids"] = st.get("done_ids", [])[-2000:]
            st["failed_ids"] = st.get("failed_ids", [])[-2000:]
            _save_state(st)
        except Exception as e:
            print(f"[LinkedInEnrich] loop error: {str(e)[:100]}")
        time.sleep(ENRICH_INTERVAL_S)


def start_enrichment_thread():
    t = threading.Thread(target=enrichment_loop, daemon=True, name="linkedin-enrich")
    t.start()
    return t
