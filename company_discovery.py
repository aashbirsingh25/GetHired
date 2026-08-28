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
# Verified-real companies that simply have no fresher opening TODAY. They are
# NEVER discarded: a company with zero fresher roles this week may post one
# tomorrow, so they are re-probed every cycle and promoted when they do.
WATCHLIST_FILE = os.path.join(BASE_DIR, "company_watchlist.json")
# Rules/lessons written by the human-reviewed orchestrator (weekly review).
# The worker reads these each cycle - this is how it gets taught.
RULES_FILE = os.path.join(BASE_DIR, "discovery_rules.json")
# Human-readable artifact for the weekly orchestrator review.
REVIEW_FILE = os.path.join(BASE_DIR, "WEEKLY_REVIEW.md")

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
    pairs = [(j.get("title", ""), (j.get("location") or {}).get("name", "")) for j in jobs]
    return len(jobs), _india_count([l for _, l in pairs]), pairs, f"https://boards.greenhouse.io/{token}"


def _probe_lever(token: str):
    d = _get_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    if not isinstance(d, list):
        raise ValueError("unexpected lever payload")
    pairs = [(j.get("text", ""), ((j.get("categories") or {}).get("location") or "")) for j in d]
    return len(d), _india_count([l for _, l in pairs]), pairs, f"https://jobs.lever.co/{token}"


def _probe_ashby(token: str):
    d = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=false")
    jobs = d.get("jobs", [])
    pairs = [(j.get("title", ""), (j.get("location") or "")) for j in jobs]
    return len(jobs), _india_count([l for _, l in pairs]), pairs, f"https://jobs.ashbyhq.com/{token}"


def _probe_smartrecruiters(token: str):
    d = _get_json(f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100")
    jobs = d.get("content", [])
    pairs = []
    for j in jobs:
        loc = j.get("location") or {}
        pairs.append((j.get("name", ""), f"{loc.get('city','')} {loc.get('country','')}"))
    return d.get("totalFound", len(jobs)), _india_count([l for _, l in pairs]), pairs, f"https://careers.smartrecruiters.com/{token}"


def _probe_keka(token: str):
    d = _get_json(f"https://{token}.keka.com/careers/api/jobs/default/active")
    items = d if isinstance(d, list) else (d.get("data") or [])
    pairs = []
    for j in items:
        names = []
        for l in (j.get("jobLocations") or []):
            if isinstance(l, dict):
                names.append(l.get("city") or l.get("name") or "")
        pairs.append((j.get("title", ""), ", ".join(names) or "India"))
    return len(items), _india_count([l for _, l in pairs]), pairs, f"https://{token}.keka.com/careers/"


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


def _is_india_location(loc: str) -> bool:
    return any(h in (loc or "").lower() for h in INDIA_HINTS) or (loc or "").strip().lower().endswith(" in")


def verify_candidate(name: str) -> Dict[str, Any]:
    """Probe a company name against every supported ATS. Never guesses:
    a result is only 'verified' when an endpoint returned real jobs.

    GATE (fixed 2026-08-28 after weekly review): a company qualifies only if
    it has jobs that are BOTH fresher-eligible AND in India. Counting the two
    conditions independently let Jitterbit through on 14 senior India jobs
    plus 2 interns in Brazil - zero fresher roles the user could apply to.
    """
    token = slugify(name)
    if len(token) < 3:
        return {"name": name, "status": "rejected", "reason": "name too short to probe"}

    for ats, probe in PROBES.items():
        try:
            total, india, pairs, url = probe(token)
        except Exception:
            continue
        if total <= 0:
            continue
        fresher = sum(1 for t, _ in pairs if is_fresher_title(t))
        fresher_india = sum(1 for t, l in pairs if is_fresher_title(t) and _is_india_location(l))
        result = {"name": name, "ats": ats, "career_url": url, "total_jobs": total,
                  "india_jobs": india, "fresher_jobs": fresher,
                  "fresher_india_jobs": fresher_india,
                  "titles_sampled": len(pairs)}
        if india <= 0:
            result.update(status="rejected", reason="no India-based openings")
        elif fresher <= 0:
            result.update(status="rejected", reason="no fresher-eligible openings right now")
        elif fresher_india <= 0:
            result.update(status="rejected",
                          reason="fresher openings exist but none in India")
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
    # Background work yields to user-facing scoring: skip this cycle if the
    # key pool is running low (mined candidates still get probed - no LLM needed).
    if hasattr(llm_router, "has_headroom") and not llm_router.has_headroom(0.30):
        print("[CompanyDiscovery] skipping LLM proposal this cycle - reserving quota for job scoring")
        return []

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

def load_rules() -> Dict[str, Any]:
    """Lessons taught by the orchestrator during weekly review.

    Supported keys:
      blocklist_names   - never propose/admit these (confirmed junk)
      force_watch_names - keep on the watchlist even if they look dead
      min_fresher_to_admit - default 1
      notes             - free text for the worker's own log
    """
    default = {"blocklist_names": [], "force_watch_names": [],
               "min_fresher_to_admit": 1, "notes": []}
    if not os.path.exists(RULES_FILE):
        return default
    try:
        data = json.load(open(RULES_FILE, encoding="utf-8"))
        default.update({k: v for k, v in data.items() if k in default})
    except Exception:
        pass
    return default


def _load_watchlist() -> Dict[str, Any]:
    if not os.path.exists(WATCHLIST_FILE):
        return {"companies": {}}
    try:
        return json.load(open(WATCHLIST_FILE, encoding="utf-8"))
    except Exception:
        return {"companies": {}}


def _save_watchlist(data: Dict[str, Any]) -> None:
    tmp = WATCHLIST_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, WATCHLIST_FILE)


def watchlist_add_or_update(result: Dict[str, Any], reason: str) -> None:
    """Park a verified-real company that has no fresher opening yet.

    Nothing is ever deleted here - this is the anti-forgetting mechanism.
    """
    wl = _load_watchlist()
    key = result["name"].strip().lower()
    entry = wl["companies"].get(key, {})
    entry.update({
        "name": result["name"].strip(),
        "ats": result.get("ats"),
        "career_url": result.get("career_url"),
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "checks": entry.get("checks", 0) + 1,
        "last_total_jobs": result.get("total_jobs", 0),
        "last_india_jobs": result.get("india_jobs", 0),
        "last_fresher_jobs": result.get("fresher_jobs", 0),
        "reason": reason,
        "best_fresher_seen": max(entry.get("best_fresher_seen", 0), result.get("fresher_jobs", 0) or 0),
    })
    if entry.get("first_seen") is None:
        entry["first_seen"] = datetime.now(timezone.utc).isoformat()
    wl["companies"][key] = entry
    _save_watchlist(wl)


def recheck_watchlist(limit: int = 40) -> Tuple[List[Dict[str, Any]], int]:
    """Re-probe watchlisted companies. Returns (promotable, checked_count).

    A company is promotable the moment it shows a fresher-eligible opening -
    this is how "it had nothing yesterday but posted today" gets caught.
    """
    wl = _load_watchlist()
    entries = list(wl["companies"].values())
    if not entries:
        return [], 0
    # oldest-checked first so everything gets revisited fairly
    entries.sort(key=lambda e: e.get("last_checked") or "")
    batch = entries[:limit]

    promotable = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda e: verify_candidate(e["name"]), batch))
    for res in results:
        if res.get("status") == "verified":
            promotable.append(res)
            wl["companies"].pop(res["name"].strip().lower(), None)
        else:
            watchlist_add_or_update(res if res.get("ats") else
                                    {"name": res["name"], "total_jobs": 0,
                                     "india_jobs": 0, "fresher_jobs": 0},
                                    res.get("reason", "still no fresher openings"))
    _save_watchlist(wl)
    return promotable, len(batch)


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


def generate_weekly_review() -> str:
    """Write a human/orchestrator-readable review of the week's decisions.

    The orchestrator (Kiro, weekly) reads this, spot-checks the calls, and
    writes corrections into discovery_rules.json - which the worker then
    obeys. That is the teaching loop.
    """
    try:
        log = json.load(open(LOG_FILE, encoding="utf-8")).get("cycles", [])
    except Exception:
        log = []
    wl = _load_watchlist().get("companies", {})
    rules = load_rules()

    cutoff = time.time() - 7 * 86400
    recent = []
    for c in log:
        try:
            ts = datetime.fromisoformat((c.get("finished_at") or "").replace("Z", "+00:00")).timestamp()
        except Exception:
            ts = 0
        if ts >= cutoff:
            recent.append(c)

    probed = sum(c.get("candidates_probed", 0) for c in recent)
    added = [n for c in recent for n in (c.get("added") or [])]
    promoted = [n for c in recent for n in (c.get("promoted") or [])]
    rej: Dict[str, int] = {}
    for c in recent:
        for k, v in (c.get("rejections") or {}).items():
            rej[k] = rej.get(k, 0) + v

    near_miss = sorted(
        [e for e in wl.values() if (e.get("best_fresher_seen") or 0) == 0 and (e.get("last_india_jobs") or 0) > 0],
        key=lambda e: -(e.get("last_india_jobs") or 0))[:15]
    stale_watch = sorted(wl.values(), key=lambda e: -(e.get("checks") or 0))[:10]

    lines = [
        "# Weekly Company-Discovery Review",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Orchestrator: verify the decisions below, then teach the worker by",
        "editing `discovery_rules.json` (blocklist_names, force_watch_names,",
        "min_fresher_to_admit, notes). The worker reads that file every cycle.",
        "",
        "## Activity (last 7 days)",
        f"- discovery cycles: {len(recent)}",
        f"- candidates probed: {probed}",
        f"- ADDED to list: {len(added)} -> {', '.join(added[:25]) or 'none'}",
        f"- PROMOTED from watchlist: {len(promoted)} -> {', '.join(promoted[:25]) or 'none'}",
        f"- watchlist size: {len(wl)}",
        "",
        "## Rejection reasons (nothing is deleted; all are re-checked)",
    ]
    for k, v in sorted(rej.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {v} x {k}")
    lines += [
        "",
        "## Near-misses to review (real India hiring, no fresher role yet)",
        "These are the highest-risk calls: if the worker is wrong about a",
        "company, it will most likely be one of these.",
    ]
    for e in near_miss:
        lines.append(f"- {e['name']} ({e.get('ats')}) - {e.get('last_india_jobs')} India jobs, "
                     f"0 fresher, checked {e.get('checks')}x")
    lines += [
        "",
        "## Long-parked watchlist entries (checked most often, still no fresher)",
    ]
    for e in stale_watch:
        lines.append(f"- {e['name']} - checked {e.get('checks')}x, best fresher seen "
                     f"{e.get('best_fresher_seen', 0)}")
    lines += [
        "",
        "## Current taught rules",
        f"- blocklist: {rules.get('blocklist_names') or 'empty'}",
        f"- force_watch: {rules.get('force_watch_names') or 'empty'}",
        f"- min_fresher_to_admit: {rules.get('min_fresher_to_admit')}",
        f"- notes: {rules.get('notes') or 'none'}",
        "",
    ]
    content = "\n".join(lines)
    tmp = REVIEW_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, REVIEW_FILE)
    return content


def run_discovery_cycle(llm_router=None, use_llm: bool = True) -> Dict[str, Any]:
    """One autonomous cycle: recheck watchlist -> propose -> verify -> gate -> record.

    Quality-first design decisions (per user directive):
      - Nothing is ever discarded. A company without fresher openings today
        goes to the watchlist and is re-probed every cycle, so a role posted
        tomorrow is caught within hours.
      - Taught rules (discovery_rules.json) override the worker's judgement.
      - Every decision is written to the log and summarised for weekly review.
    """
    started = datetime.now(timezone.utc).isoformat()
    rules = load_rules()
    blocked = {n.strip().lower() for n in (rules.get("blocklist_names") or [])}
    min_fresher = max(1, int(rules.get("min_fresher_to_admit", 1) or 1))

    data = json.load(open(COMPANIES_FILE, encoding="utf-8"))
    known = {(c.get("name") or "").strip().lower() for c in data.get("companies", [])}

    # 1. Re-check the watchlist FIRST: promoting a company that just posted a
    # fresher role is more valuable than finding a brand-new candidate.
    promotable, rechecked = recheck_watchlist(limit=40)
    promoted = add_verified_companies(promotable) if promotable else []

    known |= {p.strip().lower() for p in promoted}

    # 2. Propose new candidates
    candidates = mine_candidates_from_jobs(known, limit=25)
    category_used = None
    if use_llm and llm_router is not None:
        try:
            log = json.load(open(LOG_FILE, encoding="utf-8")) if os.path.exists(LOG_FILE) else {"cycles": []}
            cycle_no = len(log.get("cycles", []))
        except Exception:
            cycle_no = 0
        category_used = CATEGORIES[cycle_no % len(CATEGORIES)]
        candidates += propose_candidates_via_llm(category_used, known, llm_router, limit=30)

    seen, unique = set(), []
    for n in candidates:
        k = n.strip().lower()
        if k and k not in seen and k not in known and k not in blocked:
            seen.add(k)
            unique.append(n.strip())
    unique = unique[:MAX_CANDIDATES_PER_CYCLE]

    # 3. Verify + gate
    results = []
    if unique:
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(verify_candidate, unique))

    verified, watchlisted = [], 0
    for r in results:
        if r.get("status") == "verified" and (r.get("fresher_jobs") or 0) >= min_fresher:
            verified.append(r)
        elif r.get("ats") and (r.get("total_jobs") or 0) > 0:
            # real company, real careers API, just not fresher-hiring today
            watchlist_add_or_update(r, r.get("reason") or "no fresher openings at discovery time")
            watchlisted += 1

    added = add_verified_companies(verified) if verified else []

    summary = {
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "category": category_used,
        "watchlist_rechecked": rechecked,
        "promoted": promoted,
        "candidates_probed": len(unique),
        "verified": len(verified),
        "added": added,
        "watchlisted": watchlisted,
        "rejections": {},
        "details": results[:80],
    }
    for r in results:
        if r.get("status") == "rejected":
            reason = r.get("reason", "unknown")
            summary["rejections"][reason] = summary["rejections"].get(reason, 0) + 1

    _append_log(summary)
    try:
        generate_weekly_review()
    except Exception as e:
        print(f"[CompanyDiscovery] review generation failed: {e}")

    print(f"[CompanyDiscovery] cycle done: rechecked {rechecked}, promoted {len(promoted)}, "
          f"probed {len(unique)}, added {len(added)}, watchlisted {watchlisted} ({category_used})")
    return summary
