import os
import json
import csv

BASE_DIR = os.path.dirname(__file__)
COMPANIES_FILE = os.path.join(BASE_DIR, "companies.json")
METRICS_FILE = os.path.join(BASE_DIR, "metrics.json")
CSV_FILE = os.path.join(BASE_DIR, "gethired_companies_200.csv")

initial_29_ids = {
    "google-india", "amazon-india", "microsoft-india", "razorpay", "swiggy",
    "zomato", "flipkart", "phonepe", "paytm", "cred", "meesho", "urban-company",
    "byjus", "zepto", "tcs", "infosys", "wipro", "accenture-india", "bulktestcorp1",
    "bulktestcorp2", "bulktestcorp3", "bulktestcorp4", "bulktestcorp5", "bulktestcorp6",
    "bulktestcorp7", "bulktestcorp8", "bulktestcorp9", "bulktestcorp10", "netflix-india"
}

def main():
    with open(COMPANIES_FILE, "r", encoding="utf-8") as f:
        comps_data = json.load(f)

    with open(METRICS_FILE, "r", encoding="utf-8") as f:
        metrics_data = json.load(f)

    companies = comps_data.get("companies", [])
    metrics = metrics_data.get("companies", {})

    newly_added_comps = [c for c in companies if c.get("id") not in initial_29_ids]

    broken_url_list = []
    no_current_openings_list = []

    for c in newly_added_comps:
        cid = c["id"]
        cname = c["name"]
        url = c.get("career_url", "")
        m = metrics.get(cid, {})
        jobs_extracted = m.get("jobs_extracted", 0)
        succ_scans = m.get("successful_scans", 0)
        errors = m.get("errors", [])
        last_error = errors[-1] if errors else None

        if succ_scans > 0 and jobs_extracted > 0:
            continue

        item = {
            "id": cid,
            "name": cname,
            "career_url": url,
            "last_error": last_error
        }

        # Categorize
        if last_error and ("Timeout" in last_error or "Error opening page" in last_error or "failed" in last_error.lower() and "no jobs found" not in last_error.lower()):
            item["category"] = "broken_url"
            item["reason"] = last_error
            broken_url_list.append(item)
        else:
            item["category"] = "no_current_openings"
            item["reason"] = last_error or "0 jobs found on page using heuristic extraction"
            no_current_openings_list.append(item)

    print("=" * 80)
    print("CATEGORIZED FAILURE REPORT FOR NEWLY ADDED COMPANIES")
    print("=" * 80)
    print(f"Total Newly Added Failed Companies: {len(broken_url_list) + len(no_current_openings_list)}")
    print(f"  1. broken_url (Page load timeout/error/404): {len(broken_url_list)}")
    print(f"  2. no_current_openings (Page loaded, 0 jobs found): {len(no_current_openings_list)}")
    print("=" * 80)

    print(f"\n1. CATEGORY: BROKEN URL / LOAD ERROR ({len(broken_url_list)} companies):")
    print("These companies failed to load or timed out. Their URLs need fixing/updating.")
    print("-" * 80)
    for idx, b in enumerate(broken_url_list, 1):
        print(f"{idx:2d}. [{b['id']}] {b['name']}")
        print(f"    URL: {b['career_url']}")
        print(f"    Error: {b['reason']}\n")

    print("=" * 80)
    print(f"2. CATEGORY: NO CURRENT OPENINGS / HEURISTIC ZERO ({len(no_current_openings_list)} companies):")
    print("These pages loaded successfully but returned 0 job listings.")
    print("-" * 80)
    for idx, n in enumerate(no_current_openings_list, 1):
        print(f"{idx:2d}. [{n['id']}] {n['name']} | URL: {n['career_url']}")

    # Save breakdown JSON
    out_file = os.path.join(BASE_DIR, "scratch", "failed_companies_categorized.json")
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "broken_url": broken_url_list,
            "no_current_openings": no_current_openings_list
        }, f, indent=2)

if __name__ == "__main__":
    main()
