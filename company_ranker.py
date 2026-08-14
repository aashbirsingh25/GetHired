import json
import os

COMPANIES_FILE = os.path.join(os.path.dirname(__file__), "companies.json")
SCAN_ORDER_FILE = os.path.join(os.path.dirname(__file__), "scan_order.json")

def load_companies(filepath=COMPANIES_FILE):
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("companies", [])
    return []

def rank_companies(companies=None):
    if companies is None:
        companies = load_companies()

    def get_diff(c):
        val = c.get("difficulty_estimate")
        return val if val is not None else 0.8

    def get_pref(c):
        val = c.get("your_preference_score")
        return val if val is not None else 0.5

    # Ranking logic:
    # Sort criteria:
    # 1. your_preference_score (descending)
    # 2. success_rate (descending)
    # 3. avg_salary_inr (descending)
    # 4. difficulty_estimate (ascending: easy 0.3 before hard 0.8)
    sorted_companies = sorted(
        companies,
        key=lambda c: (
            get_pref(c),
            c.get("success_rate", 0.0) or 0.0,
            c.get("avg_salary_inr", 0) or 0,
            -get_diff(c)
        ),
        reverse=True
    )

    scan_order = []
    for rank, comp in enumerate(sorted_companies, start=1):
        pref = get_pref(comp)
        succ = (comp.get("success_rate", 0.0) or 0.0) * 100
        sal = comp.get("avg_salary_inr", 0) or 0
        diff = get_diff(comp)
        
        reason = f"preference {pref:.2f} + success {succ:.0f}% + salary {sal} + difficulty {diff:.1f}"
        
        scan_order.append({
            "rank": rank,
            "id": comp.get("id"),
            "name": comp.get("name"),
            "reason": reason
        })

    result = {"scan_order": scan_order}
    
    with open(SCAN_ORDER_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    return sorted_companies, result

if __name__ == "__main__":
    sorted_comps, result = rank_companies()
    print(f"Ranked {len(result['scan_order'])} companies:")
    for item in result["scan_order"][:5]:
        print(f"Rank {item['rank']}: {item['name']} ({item['reason']})")
