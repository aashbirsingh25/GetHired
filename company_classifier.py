import json
import os

COMPANIES_FILE = os.path.join(os.path.dirname(__file__), "companies.json")

def update_company_difficulty(company_id: str, jobs_extracted_count: int, is_success: bool):
    """
    Classify difficulty:
    - If successful and extracted >= 5 jobs (or successful heuristic match) -> "easy" (0.3)
    - If fails or < 5 jobs -> "hard" (0.8)
    """
    if not os.path.exists(COMPANIES_FILE):
        return

    with open(COMPANIES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    companies = data.get("companies", [])
    updated = False

    for comp in companies:
        if comp.get("id") == company_id:
            if is_success and jobs_extracted_count >= 5:
                comp["difficulty_estimate"] = 0.3
            else:
                comp["difficulty_estimate"] = 0.8
            updated = True
            break

    if updated:
        with open(COMPANIES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

def classify_all_from_metrics(metrics_data: dict):
    """
    Classify all companies based on latest metrics data.
    """
    if not os.path.exists(COMPANIES_FILE):
        return

    with open(COMPANIES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    companies = data.get("companies", [])
    company_metrics = metrics_data.get("companies", {})

    for comp in companies:
        cid = comp.get("id")
        if cid in company_metrics:
            m = company_metrics[cid]
            jobs_count = m.get("jobs_extracted", 0)
            succ = m.get("successful_scans", 0) > 0
            if succ and jobs_count >= 5:
                comp["difficulty_estimate"] = 0.3
            else:
                comp["difficulty_estimate"] = 0.8

    with open(COMPANIES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
