import os
import json
import shutil
import hashlib
from datetime import datetime
from typing import Dict, Any, List, Set

from job_identity import generate_canonical_job_id, normalize_url
from job_deduplicator import JobDeduplicator
from store_integrity_checker import check_job_posting_validity

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_STORE_FILE = os.path.join(BASE_DIR, "jobs_store.json")
JOBS_CURATED_FILE = os.path.join(BASE_DIR, "jobs_curated.json")
APPLICATIONS_FILE = os.path.join(BASE_DIR, "applications.json")
SAVED_JOBS_FILE = os.path.join(BASE_DIR, "saved_jobs.json")
VIEWED_JOBS_FILE = os.path.join(BASE_DIR, "viewed_jobs.json")
RESUME_FILE = os.path.join(BASE_DIR, "resume_store.json")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")

def load_json(filepath: str, default: Any) -> Any:
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def save_json(filepath: str, data: Any):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def backup_data_files() -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_dir = os.path.join(BACKUP_DIR, f"backup_{timestamp}")
    os.makedirs(target_dir, exist_ok=True)

    files_to_backup = [
        JOBS_STORE_FILE, JOBS_CURATED_FILE, APPLICATIONS_FILE,
        SAVED_JOBS_FILE, VIEWED_JOBS_FILE, RESUME_FILE
    ]

    for fpath in files_to_backup:
        if os.path.exists(fpath):
            shutil.copy2(fpath, os.path.join(target_dir, os.path.basename(fpath)))

    print(f"[Migration] Created backup in: {target_dir}")
    return target_dir

def run_store_migration():
    print("==================================================")
    print("STARTING DETERMINISTIC STORE MIGRATION & CLEANUP")
    print("==================================================")

    # 1. Create Backup
    backup_path = backup_data_files()

    # 2. Load Raw Stores
    store_data = load_json(JOBS_STORE_FILE, {"jobs": []})
    raw_jobs = store_data.get("jobs", [])

    curated_data = load_json(JOBS_CURATED_FILE, {"jobs": []})
    curated_raw = curated_data.get("jobs", [])

    apps_data = load_json(APPLICATIONS_FILE, {"applications": []})
    applications = apps_data.get("applications", [])

    saved_data = load_json(SAVED_JOBS_FILE, {"saved_jobs": []})
    saved_list = saved_data.get("saved_jobs", [])

    viewed_data = load_json(VIEWED_JOBS_FILE, {"viewed_jobs": []})
    viewed_list = viewed_data.get("viewed_jobs", [])

    resume_data = load_json(RESUME_FILE, {})
    current_resume_hash = resume_data.get("version_hash")

    # Combine all known raw jobs to find complete set
    all_raw_jobs = raw_jobs + curated_raw
    total_raw_count = len(all_raw_jobs)

    # Build Mapping of Old ID -> Canonical Job Object
    old_id_to_canon_id: Dict[str, str] = {}
    valid_jobs_for_dedup: List[Dict[str, Any]] = []

    missing_company = 0
    missing_title = 0
    invalid_url = 0
    stale_score_count = 0

    for raw in all_raw_jobs:
        old_id = raw.get("id") or raw.get("job_id")
        comp = raw.get("company")
        title = raw.get("title")
        url = raw.get("url")

        if not comp:
            missing_company += 1
        if not title:
            missing_title += 1
        if not url or len(str(url).strip()) < 5:
            invalid_url += 1

        is_valid, _ = check_job_posting_validity(raw)
        if not is_valid:
            continue

        canon_id = generate_canonical_job_id(raw)
        if old_id:
            old_id_to_canon_id[old_id] = canon_id

        # Check score freshness
        match_obj = raw.get("match")
        if match_obj:
            match_hash = match_obj.get("resume_version_hash") or match_obj.get("resume_hash")
            if current_resume_hash and match_hash != current_resume_hash:
                stale_score_count += 1
                raw["match"] = None

        valid_jobs_for_dedup.append(raw)

    # 3. Deduplicate All Jobs via JobDeduplicator
    deduplicator = JobDeduplicator()
    deduped_jobs, metrics = deduplicator.deduplicate(valid_jobs_for_dedup)

    unique_canonical_count = len(deduped_jobs)
    duplicates_merged = metrics["duplicates_removed"]

    canonical_job_ids: Set[str] = {j["id"] for j in deduped_jobs}
    canon_job_map: Dict[str, Dict[str, Any]] = {j["id"]: j for j in deduped_jobs}

    # 4. Migrate Applications to Canonical IDs
    migrated_apps = []
    seen_app_canon_ids = set()
    orphan_apps = 0

    for app in applications:
        old_jid = app.get("job_id")
        canon_jid = old_id_to_canon_id.get(old_jid, old_jid)

        if canon_jid not in canonical_job_ids:
            orphan_apps += 1

        app["job_id"] = canon_jid
        
        # Deduplicate multiple application records for same canonical job
        if canon_jid not in seen_app_canon_ids:
            seen_app_canon_ids.add(canon_jid)
            migrated_apps.append(app)

    # 5. Migrate Saved Jobs
    migrated_saved = []
    for old_s in saved_list:
        canon_s = old_id_to_canon_id.get(old_s, old_s)
        if canon_s not in migrated_saved:
            migrated_saved.append(canon_s)

    # 6. Migrate Viewed Jobs
    migrated_viewed = []
    seen_viewed_ids = set()
    for item in viewed_list:
        old_v = item.get("job_id")
        canon_v = old_id_to_canon_id.get(old_v, old_v)
        if canon_v not in seen_viewed_ids:
            seen_viewed_ids.add(canon_v)
            item_copy = dict(item)
            item_copy["job_id"] = canon_v
            migrated_viewed.append(item_copy)

    # 7. Save Cleaned & Migrated Stores
    save_json(JOBS_STORE_FILE, {"jobs": deduped_jobs})
    save_json(JOBS_CURATED_FILE, {"last_search": datetime.now().isoformat(), "jobs": deduped_jobs})
    save_json(APPLICATIONS_FILE, {"applications": migrated_apps})
    save_json(SAVED_JOBS_FILE, {"saved_jobs": migrated_saved})
    save_json(VIEWED_JOBS_FILE, {"viewed_jobs": migrated_viewed})

    report = {
        "backup_location": backup_path,
        "total_raw_jobs": total_raw_count,
        "unique_canonical_jobs": unique_canonical_count,
        "duplicates_removed": duplicates_merged,
        "missing_company": missing_company,
        "missing_title": missing_title,
        "invalid_url": invalid_url,
        "stale_scores_invalidated": stale_score_count,
        "applications_migrated": len(migrated_apps),
        "orphan_applications": orphan_apps,
        "saved_jobs_migrated": len(migrated_saved),
        "viewed_jobs_migrated": len(migrated_viewed)
    }

    print("\n--- MIGRATION REPORT ---")
    for k, v in report.items():
        print(f"  {k}: {v}")
    print("==================================================")
    return report

if __name__ == "__main__":
    run_store_migration()
