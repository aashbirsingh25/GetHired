import os
import json
import time
from datetime import datetime

from scan_coordinator import ScanCoordinator, load_json, save_json

BASE_DIR = os.path.dirname(__file__)
COMPANIES_FILE = os.path.join(BASE_DIR, "companies.json")
METRICS_FILE = os.path.join(BASE_DIR, "metrics.json")
JOBS_STORE_FILE = os.path.join(BASE_DIR, "jobs_store.json")

def main():
    print("=" * 80)
    print("RE-SCANNING FAILED / 0-JOB COMPANIES WITH ENHANCED BROWSER SCANNER")
    print("=" * 80)

    companies_data = load_json(COMPANIES_FILE, {"companies": []})
    companies = companies_data.get("companies", [])
    metrics = load_json(METRICS_FILE, {"companies": {}}).get("companies", {})

    target_companies = []
    before_counts = {}

    for c in companies:
        cid = c["id"]
        # Skip bot-protected
        if c.get("bot_protected"):
            continue

        m = metrics.get(cid, {})
        jobs_extracted = m.get("jobs_extracted", 0)
        succ_scans = m.get("successful_scans", 0)

        # Target companies with 0 jobs extracted / failed scans
        if succ_scans == 0 or jobs_extracted == 0:
            target_companies.append(c)
            before_counts[cid] = jobs_extracted

    print(f"Total 0-job/failed companies to rescan (excluding bot_protected): {len(target_companies)}")
    
    coordinator = ScanCoordinator()
    
    flipped_companies = []
    still_failed = []
    
    start_time = datetime.now()

    for idx, comp in enumerate(target_companies, 1):
        cid = comp["id"]
        cname = comp["name"]
        url = comp.get("career_url")
        print(f"[{idx}/{len(target_companies)}] Rescanning [{cid}]: {cname}...")

        # Run scan for this specific company
        stored_pattern = coordinator.pattern_store.get_pattern(cid)
        jobs, learned_pattern, method, error_msg = coordinator.scanner.scan_company(comp, stored_pattern)

        is_success = len(jobs) > 0 and error_msg is None

        # Update Pattern Store
        if is_success and learned_pattern and method == "heuristic":
            coordinator.pattern_store.save_pattern(
                cid,
                learned_pattern.get("job_card_selector", ""),
                learned_pattern.get("title_selector", ""),
                learned_pattern.get("location_selector", ""),
                learned_pattern.get("apply_link_selector", "")
            )

        # Update Metrics
        m_comp = metrics.get(cid, {
            "total_scans": 0,
            "successful_scans": 0,
            "jobs_extracted": 0,
            "parsing_accuracy": 0.0,
            "last_scan": None,
            "extraction_method": method,
            "errors": []
        })

        now_iso = datetime.now().isoformat()
        m_comp["total_scans"] += 1
        if is_success:
            m_comp["successful_scans"] += 1
            m_comp["jobs_extracted"] = len(jobs)
        else:
            m_comp["jobs_extracted"] = len(jobs)
            
        m_comp["parsing_accuracy"] = round(m_comp["successful_scans"] / m_comp["total_scans"], 2)
        m_comp["last_scan"] = now_iso
        m_comp["extraction_method"] = method
        m_comp["errors"].append(error_msg)
        metrics[cid] = m_comp

        # Update Company Record
        from company_classifier import update_company_difficulty
        update_company_difficulty(cid, len(jobs), is_success)

        # Append newly extracted jobs to jobs_store.json
        if is_success and len(jobs) > 0:
            jobs_store = load_json(JOBS_STORE_FILE, {"jobs": []})
            existing_jobs = {j["id"]: j for j in jobs_store.get("jobs", [])}
            for j in jobs:
                existing_jobs[j["id"]] = j
            jobs_store["jobs"] = list(existing_jobs.values())
            save_json(JOBS_STORE_FILE, jobs_store)

            flipped_companies.append({
                "id": cid,
                "name": cname,
                "url": url,
                "before": before_counts[cid],
                "after": len(jobs),
                "method": method
            })
            print(f"  ==> FLIPPED SUCCESS! [{cname}] extracted {len(jobs)} jobs!")
        else:
            still_failed.append({
                "id": cid,
                "name": cname,
                "url": url,
                "reason": error_msg or "0 jobs found"
            })
            print(f"  ==> Still 0 jobs. ({error_msg})")

        # Save metrics & companies state incrementally
        save_json(METRICS_FILE, {"companies": metrics})

    coordinator.scanner.close()

    duration = (datetime.now() - start_time).total_seconds()
    print(f"\nRescan complete in {duration:.1f} seconds.")

    # Compute overall status across full 212 directory
    updated_companies = load_json(COMPANIES_FILE, {"companies": []}).get("companies", [])
    updated_metrics = load_json(METRICS_FILE, {"companies": {}}).get("companies", {})
    updated_jobs = load_json(JOBS_STORE_FILE, {"jobs": []}).get("jobs", [])

    total_dir = len(updated_companies)
    total_succeeded = 0
    total_failed = 0
    bot_protected_count = 0

    for c in updated_companies:
        cid = c["id"]
        if c.get("bot_protected"):
            bot_protected_count += 1
            total_failed += 1
            continue

        m = updated_metrics.get(cid, {})
        jobs_extracted = m.get("jobs_extracted", 0)
        succ_scans = m.get("successful_scans", 0)

        if succ_scans > 0 and jobs_extracted > 0:
            total_succeeded += 1
        else:
            total_failed += 1

    print("\n" + "=" * 80)
    print("RE-SCAN RESULTS SUMMARY REPORT")
    print("=" * 80)
    print(f"Companies Rescanned: {len(target_companies)}")
    print(f"Companies Flipped from 0 -> >0 Jobs: {len(flipped_companies)}")
    print("-" * 80)

    if flipped_companies:
        print("\nFLIPPED COMPANIES (0 Jobs -> Jobs Found):")
        for f in flipped_companies:
            print(f"  - [{f['id']}] {f['name']}: {f['before']} -> {f['after']} jobs (URL: {f['url']})")

    print("\n" + "=" * 80)
    print(f"UPDATED OVERALL DIRECTORY STATS ({total_dir} Companies)")
    print("=" * 80)
    print(f"  - Total Companies in Directory: {total_dir}")
    print(f"  - Total Scanned Successfully (Jobs > 0): {total_succeeded} ({(total_succeeded/total_dir)*100:.1f}%)")
    print(f"  - Total Failed / 0 Jobs: {total_failed} (Includes {bot_protected_count} bot_protected)")
    print(f"  - Total Jobs in jobs_store.json: {len(updated_jobs)}")
    print("=" * 80)

    # Save summary report to scratch
    out_file = os.path.join(BASE_DIR, "scratch", "rescan_flip_report.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "target_companies_count": len(target_companies),
            "flipped_companies_count": len(flipped_companies),
            "flipped_companies": flipped_companies,
            "total_directory_companies": total_dir,
            "total_succeeded": total_succeeded,
            "total_failed": total_failed,
            "total_jobs_in_store": len(updated_jobs),
            "duration_seconds": round(duration, 1)
        }, f, indent=2)

if __name__ == "__main__":
    main()
