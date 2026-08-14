import os
import json
from datetime import datetime
from typing import Dict, Any, List

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_FILE = os.path.join(BASE_DIR, "jobs_store.json")
JOBS_CURATED_FILE = os.path.join(BASE_DIR, "jobs_curated.json")
APPLICATIONS_FILE = os.path.join(BASE_DIR, "applications.json")
RESUME_FILE = os.path.join(BASE_DIR, "resume_store.json")
FILTERS_FILE = os.path.join(BASE_DIR, "filters.json")

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def run_diagnostics():
    store_data = load_json(JOBS_FILE, {"jobs": []})
    jobs_list = store_data.get("jobs", [])
    
    curated_data = load_json(JOBS_CURATED_FILE, {"jobs": []})
    curated_list = curated_data.get("jobs", [])

    apps_data = load_json(APPLICATIONS_FILE, {"applications": []})
    apps_list = apps_data.get("applications", [])

    resume_data = load_json(RESUME_FILE, {})
    current_resume_hash = resume_data.get("version_hash") or "none"
    resume_chunks = resume_data.get("chunk_count", 0)

    # Calculate metrics
    raw_count = len(jobs_list)
    unique_ids = set()
    dup_count = 0
    invalid_count = 0
    stale_score_count = 0
    current_score_count = 0
    source_counts = {}

    for j in jobs_list:
        jid = j.get("id") or j.get("job_id")
        if not jid or jid in unique_ids:
            dup_count += 1
        if jid:
            unique_ids.add(jid)

        comp = j.get("company")
        title = j.get("title")
        url = j.get("url")
        if not comp or not title or not url:
            invalid_count += 1

        src = j.get("source") or "unknown"
        source_counts[src] = source_counts.get(src, 0) + 1

        match = j.get("match")
        if match:
            m_hash = match.get("resume_version_hash") or match.get("resume_hash")
            if m_hash == current_resume_hash and current_resume_hash != "none":
                current_score_count += 1
            else:
                stale_score_count += 1

    app_job_ids = {a.get("job_id") for a in apps_list if a.get("job_id") and a.get("status") != "archived"}
    orphan_apps = [a for a in apps_list if a.get("job_id") not in unique_ids and a.get("status") != "archived"]

    # Check leaky applied jobs in feed
    leaking_applied = [j for j in jobs_list if j.get("id") in app_job_ids]

    print("==================================================")
    print("SYSTEM HEALTH DIAGNOSTIC")
    print("==================================================")
    print(f"Raw jobs in store:                {raw_count}")
    print(f"Unique canonical jobs:            {len(unique_ids)}")
    print(f"Duplicate IDs found:              {dup_count}")
    print(f"Invalid / incomplete jobs:        {invalid_count}")
    print(f"Current resume hash:              {current_resume_hash}")
    print(f"Current resume chunks:            {resume_chunks}")
    print(f"Jobs scored for current resume:   {current_score_count}")
    print(f"Jobs scored with stale resume:    {stale_score_count}")
    print(f"Applications count:               {len(apps_list)}")
    print(f"Orphan applications:              {len(orphan_apps)}")
    print(f"Applied jobs leaking into feed:   {len(leaking_applied)}")
    print("API/source counts:")
    for src, cnt in source_counts.items():
        print(f"  - {src}: {cnt}")
    print("--------------------------------------------------")

    print("\nTOP 20 FEED JOBS")
    print("--------------------------------------------------")
    # Sort top jobs by match score
    sorted_jobs = sorted(jobs_list, key=lambda x: (x.get("match") or {}).get("score", 0), reverse=True)
    
    for idx, j in enumerate(sorted_jobs[:20], 1):
        jid = j.get("id") or "N/A"
        comp = j.get("company", "Unknown")
        title = j.get("title", "Unknown")
        loc = j.get("location", "Unknown")
        pdate = (j.get("first_seen") or j.get("posted_date") or "")[:10]
        match_obj = j.get("match") or {}
        score = match_obj.get("score", 0)
        r_hash = match_obj.get("resume_version_hash") or match_obj.get("resume_hash") or "unscored"
        method = match_obj.get("scoring_method") or match_obj.get("tier_selected") or match_obj.get("llm_used") or "N/A"
        applied = "YES" if jid in app_job_ids else "NO"
        source = j.get("source") or "web"
        url = (j.get("canonical_url") or j.get("url") or "")[:40]

        print(f"{idx:02d}. [{jid[:12]}] {title[:30]} | {comp[:20]} | {loc[:15]} | Score: {score}% | ResumeHash: {r_hash[:8]} | Method: {method} | Applied: {applied} | Src: {source}")

    print("==================================================")

if __name__ == "__main__":
    run_diagnostics()
