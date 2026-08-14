import os
import json
import io
import sys
from datetime import datetime

from app import app
from scan_coordinator import ScanCoordinator, load_json, save_json

BASE_DIR = os.path.dirname(__file__)
CSV_FILE = os.path.join(BASE_DIR, "gethired_companies_200.csv")
COMPANIES_FILE = os.path.join(BASE_DIR, "companies.json")
METRICS_FILE = os.path.join(BASE_DIR, "metrics.json")
JOBS_STORE_FILE = os.path.join(BASE_DIR, "jobs_store.json")
JOBS_CURATED_FILE = os.path.join(BASE_DIR, "jobs_curated.json")

def main():
    print("=" * 80)
    print("STEP 1 & 2: BULK IMPORTING 200 COMPANIES VIA POST /api/companies/bulk-import")
    print("=" * 80)

    if not os.path.exists(CSV_FILE):
        print(f"Error: {CSV_FILE} not found!")
        sys.exit(1)

    initial_companies_data = load_json(COMPANIES_FILE, {"companies": []})
    initial_companies = initial_companies_data.get("companies", [])
    initial_company_ids = {c.get("id") for c in initial_companies}
    initial_count = len(initial_companies)
    print(f"Initial company count in companies.json: {initial_count}")

    # Initialize Flask test client
    client = app.test_client()
    client.testing = True

    # Read CSV and inspect imported items vs duplicates before/after
    with open(CSV_FILE, "rb") as f:
        file_bytes = f.read()

    data = {
        "file": (io.BytesIO(file_bytes), "gethired_companies_200.csv")
    }

    import_res = client.post("/api/companies/bulk-import", data=data, content_type="multipart/form-data")
    print(f"Bulk import HTTP response status: {import_res.status_code}")
    import_json = import_res.get_json()
    print("Bulk import API response summary:")
    print(json.dumps(import_json, indent=2))

    # STEP 3: Verify GET /api/companies & detail duplicates/invalid URLs
    print("\n" + "=" * 80)
    print("STEP 3: VERIFICATION OF IMPORTED COMPANIES")
    print("=" * 80)

    get_res = client.get("/api/companies")
    current_companies_data = get_res.get_json()
    current_companies = current_companies_data.get("companies", [])
    total_companies_now = len(current_companies)

    print(f"Total companies now via GET /api/companies: {total_companies_now}")

    # Determine newly added company objects
    newly_added = [c for c in current_companies if c.get("id") not in initial_company_ids]
    newly_added_ids = {c.get("id") for c in newly_added}
    print(f"Newly added company count: {len(newly_added)}")

    # Parse raw CSV to find skipped duplicates or invalid URLs
    import csv
    raw_csv_rows = []
    reader = csv.DictReader(io.StringIO(file_bytes.decode("utf-8", errors="ignore")))
    for row in reader:
        raw_csv_rows.append(row)

    print(f"Total rows in CSV: {len(raw_csv_rows)}")

    skipped_duplicates_list = []
    invalid_urls_list = []

    # Re-evaluate CSV rows against initial state to detail specific skipped/invalid items
    seen_names = {c.get("name", "").lower().strip() for c in initial_companies}
    seen_urls = {c.get("career_url", "").lower().strip() for c in initial_companies}

    for row in raw_csv_rows:
        name = (row.get("name") or row.get("Company") or "").strip()
        url = (row.get("career_url") or row.get("url") or "").strip()
        if not name or not url or not (url.startswith("http://") or url.startswith("https://") or "." in url):
            invalid_urls_list.append({"name": name, "career_url": url, "reason": "Invalid or missing URL"})
            continue
        if not (url.startswith("http://") or url.startswith("https://")):
            url = f"https://{url}"

        if name.lower() in seen_names or url.lower() in seen_urls:
            skipped_duplicates_list.append({"name": name, "career_url": url, "reason": "Duplicate name or URL"})
        else:
            seen_names.add(name.lower())
            seen_urls.add(url.lower())

    print(f"\nSummary Report:")
    print(f"  Added: {import_json.get('added')}")
    print(f"  Skipped Duplicates: {import_json.get('skipped_duplicates')}")
    print(f"  Invalid URLs: {import_json.get('invalid_urls')}")
    print(f"  Total Companies Now: {import_json.get('total_companies_now')}")

    if skipped_duplicates_list:
        print(f"\nExplicit Skipped Duplicates ({len(skipped_duplicates_list)}):")
        for dup in skipped_duplicates_list:
            print(f"  - {dup['name']} ({dup['career_url']})")

    if invalid_urls_list:
        print(f"\nExplicit Invalid URLs ({len(invalid_urls_list)}):")
        for inv in invalid_urls_list:
            print(f"  - {inv['name']} ({inv['career_url']})")

    # STEP 4 & 5: Trigger scan cycle & check company_classifier integration
    print("\n" + "=" * 80)
    print("STEP 4 & 5: EXECUTING ONE FULL MANUAL SCAN CYCLE")
    print("=" * 80)
    print("Starting full scan cycle across all target companies using ScanCoordinator...")
    
    jobs_store_before = load_json(JOBS_STORE_FILE, {"jobs": []}).get("jobs", [])
    jobs_curated_before = load_json(JOBS_CURATED_FILE, {"jobs": []}).get("jobs", [])
    jobs_store_count_before = len(jobs_store_before)
    jobs_curated_count_before = len(jobs_curated_before)

    start_scan_time = datetime.now()
    coordinator = ScanCoordinator()
    coordinator.run_scan()
    end_scan_time = datetime.now()
    scan_duration = (end_scan_time - start_scan_time).total_seconds()
    print(f"\nScan cycle completed in {scan_duration:.1f} seconds.")

    # STEP 6: Post-scan report & metric synthesis
    print("\n" + "=" * 80)
    print("STEP 6: POST-SCAN RESULTS AND ACCURACY REPORT")
    print("=" * 80)

    updated_metrics = load_json(METRICS_FILE, {"companies": {}}).get("companies", {})
    updated_companies_data = load_json(COMPANIES_FILE, {"companies": []})
    updated_companies = updated_companies_data.get("companies", [])

    jobs_store_after = load_json(JOBS_STORE_FILE, {"jobs": []}).get("jobs", [])
    jobs_curated_after = load_json(JOBS_CURATED_FILE, {"jobs": []}).get("jobs", [])

    succeeded_companies = []
    failed_companies = []
    newly_added_accuracies = []

    for comp in updated_companies:
        cid = comp["id"]
        cname = comp["name"]
        m_entry = updated_metrics.get(cid, {})
        jobs_extracted = m_entry.get("jobs_extracted", 0)
        succ_scans = m_entry.get("successful_scans", 0)
        accuracy = m_entry.get("parsing_accuracy", 0.0)
        errors = m_entry.get("errors", [])
        last_error = errors[-1] if errors else None

        if succ_scans > 0 and jobs_extracted > 0:
            succeeded_companies.append({
                "id": cid,
                "name": cname,
                "jobs_extracted": jobs_extracted,
                "difficulty_estimate": comp.get("difficulty_estimate")
            })
        else:
            failed_companies.append({
                "id": cid,
                "name": cname,
                "url": comp.get("career_url"),
                "reason": last_error or "0 jobs found"
            })

        if cid in newly_added_ids:
            newly_added_accuracies.append({
                "id": cid,
                "name": cname,
                "jobs_extracted": jobs_extracted,
                "parsing_accuracy": accuracy,
                "difficulty_estimate": comp.get("difficulty_estimate")
            })

    print(f"\nTotal Target Companies Scanned: {len(updated_companies)}")
    print(f"  - Scanned Successfully (jobs > 0): {len(succeeded_companies)}")
    print(f"  - Failed (0 jobs / errors): {len(failed_companies)}")

    print(f"\nCompanies Failed to Parse ({len(failed_companies)} total):")
    for f_comp in failed_companies:
        print(f"  - [{f_comp['id']}] {f_comp['name']}: {f_comp['reason']} (URL: {f_comp['url']})")

    # Newly Added Companies Specific Parsing Accuracy Summary
    new_succ_count = sum(1 for item in newly_added_accuracies if item["jobs_extracted"] > 0)
    new_total_extracted = sum(item["jobs_extracted"] for item in newly_added_accuracies)
    avg_accuracy = (
        sum(item["parsing_accuracy"] for item in newly_added_accuracies) / len(newly_added_accuracies)
        if newly_added_accuracies else 0.0
    )

    print(f"\nParsing Accuracy Summary for Newly Added Companies ({len(newly_added_ids)} companies):")
    print(f"  - Newly Added Successful: {new_succ_count}/{len(newly_added_ids)}")
    print(f"  - Total Jobs Extracted from New Companies: {new_total_extracted}")
    print(f"  - Average Parsing Accuracy Metric: {avg_accuracy:.2f}")

    print(f"\nJob Store Updates:")
    print(f"  - jobs_store.json: {jobs_store_count_before} -> {len(jobs_store_after)} jobs (Net added: {len(jobs_store_after) - jobs_store_count_before})")
    print(f"  - jobs_curated.json: {jobs_curated_count_before} -> {len(jobs_curated_after)} jobs (Net added: {len(jobs_curated_after) - jobs_curated_count_before})")

if __name__ == "__main__":
    main()
