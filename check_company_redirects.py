import json
import requests
from concurrent.futures import ThreadPoolExecutor

COMPANIES_FILE = "companies.json"

with open(COMPANIES_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

companies = data.get("companies", [])

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def check_one(comp):
    cid = comp["id"]
    cname = comp["name"]
    url = comp.get("career_url", "")
    if comp.get("bot_protected"):
        return None

    try:
        r = requests.get(url, headers=headers, timeout=5, allow_redirects=True)
        final_url = r.url
        if "myworkdayjobs.com" in final_url:
            return {"id": cid, "name": cname, "old_url": url, "new_url": final_url, "ats": "workday"}
        elif "greenhouse.io" in final_url:
            return {"id": cid, "name": cname, "old_url": url, "new_url": final_url, "ats": "greenhouse"}
        elif "lever.co" in final_url:
            return {"id": cid, "name": cname, "old_url": url, "new_url": final_url, "ats": "lever"}
        elif "ashbyhq.com" in final_url:
            return {"id": cid, "name": cname, "old_url": url, "new_url": final_url, "ats": "ashby"}
    except Exception:
        pass
    return None

def main():
    print("Checking redirects across 212 companies...")
    with ThreadPoolExecutor(max_workers=15) as executor:
        results = [r for r in executor.map(check_one, companies) if r is not None]

    print(f"\nFound {len(results)} companies redirecting to direct ATS portals:")
    for r in results:
        print(f"  - [{r['id']}] {r['name']} ({r['ats']}): {r['old_url']} -> {r['new_url']}")

    # Save to scratch
    with open("scratch/ats_redirects.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    main()
