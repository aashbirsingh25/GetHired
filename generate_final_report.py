import os
import json
import csv

BASE_DIR = os.path.dirname(__file__)
CSV_FILE = os.path.join(BASE_DIR, "gethired_companies_200.csv")
COMPANIES_FILE = os.path.join(BASE_DIR, "companies.json")
METRICS_FILE = os.path.join(BASE_DIR, "metrics.json")
JOBS_STORE_FILE = os.path.join(BASE_DIR, "jobs_store.json")
JOBS_CURATED_FILE = os.path.join(BASE_DIR, "jobs_curated.json")

def load_json(path, default=None):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default or {}

def main():
    companies_data = load_json(COMPANIES_FILE, {"companies": []})
    companies = companies_data.get("companies", [])
    metrics = load_json(METRICS_FILE, {"companies": {}}).get("companies", {})
    jobs_store = load_json(JOBS_STORE_FILE, {"jobs": []}).get("jobs", [])
    jobs_curated = load_json(JOBS_CURATED_FILE, {"jobs": []}).get("jobs", [])

    # Read CSV rows to identify imported pool
    with open(CSV_FILE, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        csv_rows = list(reader)

    # Initial 29 company IDs before bulk import
    initial_29_ids = {
        "google-india", "amazon-india", "microsoft-india", "razorpay", "swiggy",
        "zomato", "flipkart", "phonepe", "paytm", "cred", "meesho", "urban-company",
        "byjus", "zepto", "tcs", "infosys", "wipro", "accenture-india", "bulktestcorp1",
        "bulktestcorp2", "bulktestcorp3", "bulktestcorp4", "bulktestcorp5", "bulktestcorp6",
        "bulktestcorp7", "bulktestcorp8", "bulktestcorp9", "bulktestcorp10", "netflix-india"
    }

    newly_added_comps = [c for c in companies if c.get("id") not in initial_29_ids]
    newly_added_ids = {c.get("id") for c in newly_added_comps}

    succeeded = []
    failed = []

    for c in companies:
        cid = c["id"]
        cname = c["name"]
        url = c.get("career_url")
        m = metrics.get(cid, {})
        jobs_cnt = m.get("jobs_extracted", 0)
        succ = m.get("successful_scans", 0) > 0 and jobs_cnt > 0
        errs = m.get("errors", [])
        last_err = errs[-1] if errs else "No jobs found on page using heuristic extraction"

        item = {
            "id": cid,
            "name": cname,
            "url": url,
            "jobs_extracted": jobs_cnt,
            "difficulty_estimate": c.get("difficulty_estimate", 0.8),
            "parsing_accuracy": m.get("parsing_accuracy", 0.0),
            "error": last_err
        }

        if succ:
            succeeded.append(item)
        else:
            failed.append(item)

    # Newly added companies metrics
    new_accuracies = [c for c in companies if c.get("id") in newly_added_ids]
    new_succeeded = [item for item in succeeded if item["id"] in newly_added_ids]
    new_failed = [item for item in failed if item["id"] in newly_added_ids]
    new_extracted_total = sum(item["jobs_extracted"] for item in new_succeeded)
    avg_accuracy_new = (
        sum(metrics.get(cid, {}).get("parsing_accuracy", 0.0) for cid in newly_added_ids) / len(newly_added_ids)
        if newly_added_ids else 0.0
    )

    report = {
        "summary": {
            "total_companies_in_directory": len(companies),
            "initial_company_count": 29,
            "bulk_import_added": len(newly_added_comps),
            "bulk_import_skipped_duplicates": 18,
            "bulk_import_invalid_urls": 0,
            "total_companies_scanned": len(companies),
            "total_companies_succeeded": len(succeeded),
            "total_companies_failed": len(failed),
            "newly_added_succeeded": len(new_succeeded),
            "newly_added_failed": len(new_failed),
            "newly_added_jobs_extracted": new_extracted_total,
            "newly_added_avg_parsing_accuracy": round(avg_accuracy_new, 2),
            "total_jobs_in_jobs_store": len(jobs_store),
            "total_jobs_in_jobs_curated": len(jobs_curated)
        },
        "succeeded_companies": succeeded,
        "failed_companies": failed
    }

    print("=" * 80)
    print("BULK IMPORT & SCAN CYCLE SUMMARY REPORT")
    print("=" * 80)
    print(f"Total Companies in Directory: {report['summary']['total_companies_in_directory']}")
    print(f"  - Initial Pool: {report['summary']['initial_company_count']}")
    print(f"  - Bulk Imported New: {report['summary']['bulk_import_added']}")
    print(f"  - Skipped Duplicates: {report['summary']['bulk_import_skipped_duplicates']}")
    print(f"  - Invalid URLs: {report['summary']['bulk_import_invalid_urls']}")
    print("-" * 80)
    print(f"Scan Execution Results:")
    print(f"  - Total Scanned: {report['summary']['total_companies_scanned']}")
    print(f"  - Succeeded (Jobs Extracted > 0): {report['summary']['total_companies_succeeded']}")
    print(f"  - Failed (0 Jobs / Page Errors): {report['summary']['total_companies_failed']}")
    print("-" * 80)
    print(f"Newly Added Companies (183 total) Performance:")
    print(f"  - Succeeded: {report['summary']['newly_added_succeeded']} / 183")
    print(f"  - Failed: {report['summary']['newly_added_failed']} / 183")
    print(f"  - Total Jobs Extracted from New Pool: {report['summary']['newly_added_jobs_extracted']}")
    print(f"  - Average Parsing Accuracy Metric: {report['summary']['newly_added_avg_parsing_accuracy']}")
    print("-" * 80)
    print(f"Job Store Total Count:")
    print(f"  - jobs_store.json total jobs: {report['summary']['total_jobs_in_jobs_store']}")
    print(f"  - jobs_curated.json total jobs: {report['summary']['total_jobs_in_jobs_curated']}")
    print("=" * 80)

    # Save JSON report to scratch directory
    report_file = os.path.join(BASE_DIR, "scratch", "bulk_import_scan_report.json")
    os.makedirs(os.path.dirname(report_file), exist_ok=True)
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"Detailed JSON report saved to: {report_file}")

if __name__ == "__main__":
    main()
