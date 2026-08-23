"""Autonomous company discovery and verification.

Runs inside the app (see background_company_discovery_loop in app.py), so
the company list grows and self-cleans without any human in the loop.

Pipeline per cycle:
  1. PROPOSE  - candidate company names from two sources:
                a) mined from job postings already collected (free, and
                   every name is provably hiring)
                b) an LLM asked for companies in an under-covered category
  2. VERIFY   - live probe of each candidate's ATS API: is it real, which
                platform, how many openings, how many in India, how many
                fresher-eligible
  3. GATE     - admit only companies with India openings AND fresher-
                eligible openings right now (the user's 70-80% active goal)
  4. RECORD   - append to companies.json, log every decision (including
                rejections and why) to company_discovery_log.json

Hard rules:
  - Never fabricate a company or a career URL: a row is only written after
    its ATS endpoint returned real jobs in this run.
  - Capped additions per cycle, so a bad LLM response cannot flood the list.
  - Every rejection is logged with a reason, for auditability.
"""
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANIES_FILE = os.path.join(BASE_DIR, "companies.json")
JOBS_FILE = os.path.join(BASE_DIR, "jobs_store.json")
LOG_FILE = os.path.join(BASE_DIR, "company_discovery_log.json")

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"}

MAX_ADDS_PER_CYCLE = 25
MAX_CANDIDATES_PER_CYCLE = 60

INDIA_HINTS = ("india", "bangalore", "bengaluru", "gurugram", "gurgaon", "delhi",
               "noida", "hyderabad", "pune", "mumbai", "chennai", "apac")

SENIOR_RE = re.compile(r"\b(senior|sr\.?|lead|principal|staff|manager|director|head|vp|architect|chief)\b", re.I)
FRESHER_RE = re.compile(r"\b(intern|internship|trainee|graduate|fresher|apprentice|campus|"
                        r"entry[\s\-]?level|associate engineer|engineer\s*i\b|sde\s*[i1]\b|"
                        r"analyst\s*i\b|new\s?grad)\b", re.I)

CATEGORIES = [
    "quant trading and market-making firms with India offices",
    "Global Capability Centers of banks and financial institutions in India",
    "Global Capability Centers of retail and consumer brands in India",
    "Global Capability Centers of healthcare and pharma companies in India",
    "Indian IT services and engineering services companies",
    "Indian product startups that hire fresh graduates",
    "semiconductor, EDA and embedded systems companies in India",
    "SaaS and developer-tools companies with India engineering teams",
    "Indian fintech and payments companies",
    "Indian edtech, healthtech and agritech companies",
    "airlines, hospitality and logistics companies with India tech centers",
    "automotive and manufacturing companies with India tech centers",
]


def _get_json(url: str, timeout: int = 12):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="ignore"))


def is_fresher_title(title: str) -> bool:
    t = title or ""
    if SENIOR_RE.search(t):
        return False
    return bool(FRESHER_RE.search(t))


def _india_count(locations: List[str]) -> int:
    return sum(1 for l in locations if any(h in (l or "").lower() for h in INDIA_HINTS))


# ---------------------------------------------------------------- probes

def _probe_greenhouse(token: str):
    d = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
    jobs = d.get("jobs", [])
    titles = [j.get("title", "") for j in jobs]
    locs = [(j.get("location") or {}).get("name", "") for j in jobs]
    return len(jobs), _india_count(locs), titles, f"https://boards.greenhouse.io/{token}"


def _probe_lever(token: str):
    d = _get_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    if not isinstance(d, list):
        raise ValueError("unexpected lever payload")
    titles = [j.get("text", "") for j in d]
    locs = [((j.get("categories") or {}).get("location") or "") for j in d]
    return len(d), _india_count(locs), titles, f"https://jobs.lever.co/{token}"


def _probe_ashby(token: str):
    d = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=false")
    jobs = d.get("jobs", [])
    titles = [j.get("title", "") for j in jobs]
    locs = [(j.get("location") or "") for j in jobs]
    return len(jobs), _india_count(locs), titles, f"https://jobs.ashbyhq.com/{token}"


def _probe_smartrecruiters(token: str):
    d = _get_json(f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100")
    jobs = d.get("content", [])
    titles = [j.get("name", "") for j in jobs]
    locs = []
    for j in jobs:
        loc = j.get("location") or {}
        locs.append(f"{loc.get('city','')} {loc.get('country','')}")
    return d.get("totalFound", len(jobs)), _india_count(locs), titles, f"https://careers.smartrecruiters.com/{token}"


def _probe_keka(token: str):
    d = _get_json(f"https://{token}.keka.com/careers/api/jobs/default/active")
    items = d if isinstance(d, list) else (d.get("data") or [])
    titles = [j.get("title", "") for j in items]
    locs = []
    for j in items:
        names = []
        for l in (j.get("jobLocations") or []):
            if isinstance(l, dict):
                names.append(l.get("city") or l.get("name") or "")
        locs.append(", ".join(names) or "India")
    return len(items), _india_count(locs), titles, f"https://{token}.keka.com/careers/"


PROBES = {
    "greenhouse": _probe_greenhouse,
    "lever": _probe_lever,
    "ashby": _probe_ashby,
    "smartrecruiters": _probe_smartrecruiters,
    "keka": _probe_keka,
}


def slugify(name: str) -> str:
    s = re.sub(r"\b(pvt|private|limited|ltd|inc|llc|technologies|technology|solutions|group|india|labs)\b",
               "", (name or "").lower())
    return re.sub(r"[^a-z0-9]", "", s)


def verify_candidate(name: str) -> Dict[str, Any]:
    """Probe a company name against every supported ATS. Never guesses:
    a result is only 'verified' when an endpoint returned real jobs."""
    token = slugify(name)
    if len(token) < 3:
        return {"name": name, "status": "rejected", "reason": "name too short to probe"}

    for ats, probe in PROBES.items():
        try:
            total, india, titles, url = probe(token)
        except Exception:
            continue
        if total <= 0:
            continue
        fresher = sum(1 for t in titles if is_fresher_title(t))
        result = {"name": name, "ats": ats, "career_url": url, "total_jobs": total,
                  "india_jobs": india, "fresher_jobs": fresher,
                  "titles_sampled": len(titles)}
        if india <= 0:
            result.update(status="rejected", reason="no India-based openings")
        elif fresher <= 0:
            result.update(status="rejected", reason="no fresher-eligible openings right now")
        else:
            result.update(status="verified")
        return result
    return {"name": name, "status": "rejected", "reason": "no public ATS endpoint found"}


# ------------------------------------------------------------- proposing

def mine_candidates_from_jobs(known_names: set, limit: int = 40) -> List[str]:
    """Company names appearing in already-collected postings (provably hiring)."""
    try:
        jobs = json.load(open(JOBS_FILE, encoding="utf-8")).get("jobs", [])
    except Exception:
        return []
    junk = re.compile(r"\b(industry|solutions provider|specialist|leading|consultanc|recruit|"
                      r"staffing|hiring|placement|manpower|hr services)\b", re.I)
    seen = {}
    for j in jobs:
        name = (j.get("company") or "").strip()
        if not name or len(name) < 3 or len(name) > 45:
            continue
        if junk.search(name) or name.lower() in known_names:
            continue
        seen[name] = seen.get(name, 0) + 1
    ranked = sorted(seen.items(), key=lambda kv: -kv[1])
    return [n for n, _ in ranked[:limit]]


def propose_candidates_via_llm(category: str, known_names: set, llm_router, limit: int = 30) -> List[str]:
    """Ask the LLM for company names in a category. Names only - every one is
    verified live afterwards, so a hallucinated name simply fails the gate."""
    provider, api_key, key_idx = llm_router.get_best_available_key()
    if not provider or not api_key:
        return []

    prompt = (
        f"List {limit} real companies in this category: {category}.\n"
        "Requirements: they must genuinely have technology hiring in India, and should be "
        "companies that hire fresh graduates / entry-level engineers.\n"
        "Return ONLY a JSON array of company name strings, no commentary, no URLs.\n"
        "Prefer companies that are less obvious over the same few famous names."
    )
    try:
        if provider == "gemini":
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-flash-latest")
            raw = model.generate_content(prompt).text
        elif provider == "groq":
            from groq import Groq
            raw = Groq(api_key=api_key).chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
            ).choices[0].message.content
        else:
            return []
        llm_router.mark_used(provider, key_idx)
    except Exception as e:
        err = str(e).lower()
        cooldown = 300 if ("429" in err or "quota" in err or "rate" in err) else 60
        llm_router.on_rate_limit(provider, key_idx, cooldown_seconds=cooldown)
        print(f"[CompanyDiscovery] LLM propose error ({provider}): {str(e)[:100]}")
        return []

    m = re.search(r"\[.*\]", raw or "", re.S)
    if not m:
        return []
    try:
        names = json.loads(m.group(0))
    except Exception:
        return []
    out = []
    for n in names:
        if isinstance(n, str) and 2 < len(n.strip()) < 46 and n.strip().lower() not in known_names:
            out.append(n.strip())
    return out


# --------------------------------------------------------------- writing

def _append_log(entry: Dict[str, Any]) -> None:
    try:
        data = json.load(open(LOG_FILE, encoding="utf-8")) if os.path.exists(LOG_FILE) else {"cycles": []}
    except Exception:
        data = {"cycles": []}
    data.setdefault("cycles", []).append(entry)
    data["cycles"] = data["cycles"][-200:]
    tmp = LOG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, LOG_FILE)


def add_verified_companies(verified: List[Dict[str, Any]]) -> List[str]:
    """Append verified companies to companies.json (merge-safe, capped)."""
    data = json.load(open(COMPANIES_FILE, encoding="utf-8"))
    companies = data.get("companies", [])
    existing_ids = {c.get("id") for c in companies}
    existing_names = {(c.get("name") or "").strip().lower() for c in companies}

    added = []
    for v in verified[:MAX_ADDS_PER_CYCLE]:
        name = v["name"].strip()
        if name.lower() in existing_names:
            continue
        cid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if not cid or cid in existing_ids:
            continue
        companies.append({
            "id": cid, "name": name, "career_url": v["career_url"], "ats": v["ats"],
            "difficulty_estimate": 0.3, "your_preference_score": 0.7,
            "success_rate": 0, "avg_salary_inr": 0, "parsed_count": 0,
            "parsing_accuracy": 0.0, "last_parsed": None,
            "monitoring_status": "Active Monitoring",
            "discovered_at": datetime.now(timezone.utc).isoformat(),
            "discovery_fresher_jobs": v.get("fresher_jobs", 0),
        })
        existing_ids.add(cid)
        existing_names.add(name.lower())
        added.append(name)

    data["companies"] = companies
    tmp = COMPANIES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, COMPANIES_FILE)
    return added


def run_discovery_cycle(llm_router=None, use_llm: bool = True) -> Dict[str, Any]:
    """One autonomous cycle: propose -> verify -> gate -> record."""
    started = datetime.now(timezone.utc).isoformat()
    data = json.load(open(COMPANIES_FILE, encoding="utf-8"))
    known = {(c.get("name") or "").strip().lower() for c in data.get("companies", [])}

    candidates = mine_candidates_from_jobs(known, limit=25)

    category_used = None
    if use_llm and llm_router is not None:
        # rotate categories by cycle count so coverage stays balanced
        try:
            log = json.load(open(LOG_FILE, encoding="utf-8")) if os.path.exists(LOG_FILE) else {"cycles": []}
            cycle_no = len(log.get("cycles", []))
        except Exception:
            cycle_no = 0
        category_used = CATEGORIES[cycle_no % len(CATEGORIES)]
        candidates += propose_candidates_via_llm(category_used, known, llm_router, limit=30)

    # de-dup, cap
    seen, unique = set(), []
    for n in candidates:
        k = n.strip().lower()
        if k and k not in seen and k not in known:
            seen.add(k)
            unique.append(n.strip())
    unique = unique[:MAX_CANDIDATES_PER_CYCLE]

    results = []
    if unique:
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(verify_candidate, unique))

    verified = [r for r in results if r.get("status") == "verified"]
    added = add_verified_companies(verified) if verified else []

    summary = {
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "category": category_used,
        "candidates_probed": len(unique),
        "verified": len(verified),
        "added": added,
        "rejections": {},
        "details": results[:80],
    }
    for r in results:
        if r.get("status") == "rejected":
            reason = r.get("reason", "unknown")
            summary["rejections"][reason] = summary["rejections"].get(reason, 0) + 1

    _append_log(summary)
    print(f"[CompanyDiscovery] cycle done: probed {len(unique)}, verified {len(verified)}, "
          f"added {len(added)} ({category_used})")
    return summary
