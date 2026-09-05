import re
from typing import List, Dict, Any, Set
from job_deduplicator import JobDeduplicator
from store_integrity_checker import check_job_posting_validity
from recency_filter import filter_by_recency

def _extract_filter_strings(raw_val: Any, category_keyword: str = None) -> List[str]:
    strings = []
    if not raw_val:
        return strings
    if isinstance(raw_val, str):
        return [raw_val.strip()]
    if isinstance(raw_val, list):
        for item in raw_val:
            if isinstance(item, str) and item.strip():
                strings.append(item.strip())
            elif isinstance(item, dict):
                for cg in item.get("category_groups", []):
                    grp_name = (cg.get("group_name") or "").lower()
                    if not category_keyword or category_keyword in grp_name:
                        for v in cg.get("values", []):
                            if isinstance(v, str) and v.strip():
                                strings.append(v.strip())
    return strings

LOCATION_SYNONYMS = {
    "gurugram": ["gurugram", "gurgaon"],
    "gurgaon": ["gurugram", "gurgaon"],
    "bangalore": ["bangalore", "bengaluru"],
    "bengaluru": ["bangalore", "bengaluru"],
    "delhi ncr": ["delhi ncr", "delhi", "noida", "gurugram", "gurgaon", "ghaziabad", "faridabad"],
    "ncr": ["delhi ncr", "delhi", "noida", "gurugram", "gurgaon", "ghaziabad", "faridabad"],
}

def _matches_role_title(target_role: str, title: str) -> bool:
    if not target_role or not title:
        return False
    t_clean = title.lower().strip()
    r_clean = target_role.lower().strip()

    if t_clean == r_clean:
        return True

    if r_clean == "c":
        pattern = r"(?:^|[\s,/\(\)\[\]\-])c(?:$|[\s,/\(\)\[\]\-])"
        return bool(re.search(pattern, t_clean)) and not ("c++" in t_clean or "c#" in t_clean)

    if r_clean in ["c++", "c#", ".net"]:
        pattern = r"(?:^|[\s,/\(\)\[\]\-])" + re.escape(r_clean) + r"(?:$|[\s,/\(\)\[\]\-])"
        return bool(re.search(pattern, t_clean))

    if r_clean == "r":
        pattern = r"(?:^|[\s,/\(\)\[\]\-])r(?:$|[\s,/\(\)\[\]\-])"
        return bool(re.search(pattern, t_clean)) and "r&d" not in t_clean

    escaped = re.escape(r_clean)
    pattern = r"(?:^|[\s,/\(\)\[\]\b])" + escaped + r"(?:$|[\s,/\(\)\[\]\b])"
    return bool(re.search(pattern, t_clean))

# Patterns that state a required-experience amount. Deliberately narrow:
# bare "N years" (no range/plus/minimum context) is NOT matched, to avoid
# false positives like "launched 9 years ago" in company blurbs.
_EXP_RANGE_RE = re.compile(
    r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I)
_EXP_PLUS_RE = re.compile(r"(\d{1,2})\s*\+\s*(?:years?|yrs?)", re.I)
_EXP_MIN_RE = re.compile(
    r"(?:minimum(?:\s+of)?|at\s+least)\s+(\d{1,2})\s*(?:years?|yrs?)", re.I)

def _extract_min_experience_years(job: Dict[str, Any]) -> Any:
    """Return the minimum years of experience a job demands, or None if unstated."""
    text = f"{job.get('title') or ''} {job.get('experience_required') or ''} {(job.get('description') or '')[:4000]}"
    mins = []
    for m in _EXP_RANGE_RE.finditer(text):
        mins.append(int(m.group(1)))
    for m in _EXP_PLUS_RE.finditer(text):
        mins.append(int(m.group(1)))
    for m in _EXP_MIN_RE.finditer(text):
        mins.append(int(m.group(1)))
    return min(mins) if mins else None

def _max_user_experience_years(filters: Dict[str, Any]) -> Any:
    """Parse the user's experience ceiling from target_experience strings
    like '0-2 years', 'Fresher', '0 years'. Returns None if unconfigured."""
    raw = filters.get("target_experience") or []
    if isinstance(raw, str):
        raw = [raw]
    maxes = []
    for item in raw:
        if not isinstance(item, str):
            continue
        s = item.lower()
        if "fresher" in s or "entry" in s:
            maxes.append(0)
        for m in re.finditer(r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})", s):
            maxes.append(int(m.group(2)))
        for m in re.finditer(r"^(\d{1,2})\s*years?", s.strip()):
            maxes.append(int(m.group(1)))
    return max(maxes) if maxes else None

def _matches_location(target_loc: str, location: str, description: str = "") -> bool:
    if not target_loc:
        return True
    loc_clean = target_loc.lower().strip()
    l_lower = (location or "").lower()
    d_lower = (description or "").lower()

    if loc_clean == "remote":
        # The location FIELD must say remote. Falling back to the description
        # let any foreign on-site job that merely mentions the word (e.g.
        # "remote work possible on Fridays") into an India-only feed - Adyen
        # Amsterdam and DoorDash San Francisco were live examples. Only when
        # the job states no location at all is the description consulted.
        if "remote" in l_lower or "work from home" in l_lower or "wfh" in l_lower:
            return True
        return not l_lower.strip() and bool(re.search(r"\bremote\b", d_lower))

    synonyms = LOCATION_SYNONYMS.get(loc_clean, [loc_clean])
    for syn in synonyms:
        if re.search(r"\b" + re.escape(syn) + r"\b", l_lower):
            return True
    return False

def execute_authoritative_pipeline(
    raw_jobs: List[Dict[str, Any]],
    custom_filters: Dict[str, Any] = None,
    resume_data: Dict[str, Any] = None,
    applied_job_ids: Set[str] = None
) -> Dict[str, Any]:
    """
    Executes Phase 7 deterministic pipeline:
    RAW DISCOVERY
    ↓ NORMALIZE & DEDUPLICATE
    ↓ REMOVE INVALID/CLOSED JOBS
    ↓ CHECK RECENCY
    ↓ ROLE FILTER
    ↓ LOCATION FILTER
    ↓ EXPERIENCE FILTER
    ↓ EXCLUSION FILTER
    ↓ CURRENT RESUME SCORING (Top candidate pool)
    ↓ MINIMUM MATCH SCORE
    ↓ EXCLUDE APPLIED JOBS (for default feed)
    ↓ SORT
    ↓ FEED
    """
    # jobs the liveness checker verified as dead (separate one-writer file;
    # see job_liveness_checker._mark_closed for why not a store flag)
    try:
        import json as _json, os as _os
        with open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "closed_jobs.json"), encoding="utf-8") as _f:
            _closed_ids = set(_json.load(_f).keys())
    except Exception:
        _closed_ids = set()

    filters = custom_filters or {}
    deduplicator = JobDeduplicator()
    
    # 1. NORMALIZE & DEDUPLICATE
    deduped_jobs, dedup_metrics = deduplicator.deduplicate(raw_jobs)
    
    # 2. REMOVE INVALID / CLOSED JOBS
    valid_jobs = []
    for job in deduped_jobs:
        if job.get("closed") or job.get("id") in _closed_ids:
            continue
        is_valid, _ = check_job_posting_validity(job)
        if is_valid:
            valid_jobs.append(job)
            
    # 3. CHECK RECENCY
    hours = filters.get("upload_time_hours") or filters.get("recency_hours") or 720
    recency_jobs = filter_by_recency(valid_jobs, max_hours=hours)
    if not recency_jobs and valid_jobs:
        recency_jobs = valid_jobs

    # 4. ROLE FILTER
    raw_roles = filters.get("target_role") or filters.get("roles") or []
    target_roles = _extract_filter_strings(raw_roles, "role")
    
    role_filtered = []
    for job in recency_jobs:
        if not target_roles:
            role_filtered.append(job)
            continue
        t_title = job.get("title") or ""
        if any(_matches_role_title(r, t_title) for r in target_roles):
            role_filtered.append(job)

    # 5. LOCATION FILTER
    raw_locs = filters.get("target_location") or filters.get("locations") or []
    target_locs = _extract_filter_strings(raw_locs, "location")

    loc_filtered = []
    for job in role_filtered:
        if not target_locs:
            loc_filtered.append(job)
            continue
        j_loc = job.get("location") or ""
        j_desc = job.get("description") or ""
        if any(_matches_location(loc, j_loc, j_desc) for loc in target_locs):
            loc_filtered.append(job)

    # 6. EXPERIENCE FILTER
    # Drop jobs whose stated minimum experience exceeds the user's ceiling
    # (e.g. a '3-5 years' role for a 0-2 years candidate). Jobs that don't
    # state a requirement pass through — absence of data is not a mismatch.
    user_max_years = _max_user_experience_years(filters)
    if user_max_years is None:
        exp_filtered = loc_filtered
    else:
        exp_filtered = []
        for job in loc_filtered:
            job_min_years = _extract_min_experience_years(job)
            if job_min_years is not None and job_min_years > user_max_years:
                continue
            exp_filtered.append(job)

    # 7. EXCLUSION FILTER
    raw_excludes = filters.get("exclude_keywords")
    if raw_excludes is None:
        raw_excludes = ["Senior", "Lead", "Manager", "Principal", "Director", "Architect"]
    exclude_kws = _extract_filter_strings(raw_excludes)

    # Numbered seniority levels. Keyword matching alone let "Software
    # Engineer 3" (MongoDB), "SDE 2/3" (eBay, CloudSEK) and friends into a
    # strictly-fresher feed - level >= 2 means years of experience even though
    # no excluded WORD appears in the title. Level 1 / I is kept: SDE-1 is a
    # legitimate entry-level title. Roman numerals count too ("Engineer III").
    _leveled_seniority = re.compile(
        r"\b(?:sde|swe|sdet|mts|software\s+(?:development\s+)?engineer|engineer|developer|"
        r"software\s+dev(?:eloper)?)\s*[-‐–—]?\s*(?:level\s*)?(2|3|4|5|ii|iii|iv|v)\b",
        re.IGNORECASE)

    exclusion_filtered = []
    for job in exp_filtered:
        t_lower = (job.get("title") or "").lower()
        if any(re.search(r"\b" + re.escape(x.lower()) + r"\b", t_lower) for x in exclude_kws):
            continue
        if _leveled_seniority.search(t_lower):
            continue
        exclusion_filtered.append(job)

    # 8. CURRENT RESUME SCORING (Non-blocking: uses cached matches; background rescorer updates store)
    # A job is PENDING if it has no valid cached match, OR its cached match was
    # computed against a different resume version than the one currently active.
    # PENDING jobs are NOT scored synchronously here (that would block page load
    # and defeat the async rescorer); they are held out of the ranked feed in
    # step 9 and surfaced separately so the UI can show "N jobs still scoring".
    # PENDING only applies when there is an active resume to score against.
    # With no resume, a missing match is not "awaiting scoring" (there is
    # nothing to score), so such jobs flow through the feed unchanged.
    has_active_resume = bool((resume_data or {}).get("has_resume")) or bool((resume_data or {}).get("version_hash"))
    current_resume_hash = (resume_data or {}).get("version_hash")
    scored_jobs = []
    for job in exclusion_filtered:
        match_obj = job.get("match")
        has_valid_match = isinstance(match_obj, dict) and match_obj.get("match_grade") != "PENDING"
        is_stale = (
            has_valid_match
            and current_resume_hash is not None
            and match_obj.get("resume_version_hash") is not None
            and match_obj.get("resume_version_hash") != current_resume_hash
        )
        if has_active_resume and (not has_valid_match or is_stale):
            job_copy = dict(job)
            job_copy["match"] = {
                "score": 0,
                "match_grade": "PENDING",
                "confidence": "pending",
                "reasoning": "Awaiting background resume scoring" if not is_stale
                             else "Awaiting rescore against updated resume",
                "matched_skills": [],
                "missing_skills": []
            }
            scored_jobs.append(job_copy)
        else:
            scored_jobs.append(job)


    # 9. MINIMUM MATCH SCORE
    # PENDING jobs are separated out of the ranked feed entirely (option a):
    # they must NOT bypass min_match_score and must NOT pollute the feed at
    # score 0. They are returned separately as pending_jobs for a transparency
    # indicator; the background rescorer will give them real scores shortly,
    # after which they flow through the normal filter on the next load.
    min_score = filters.get("min_match_score", 0)
    # Evidence cap (added after the MongoDB 'Software Engineer 3' incident):
    # some sources (LinkedIn public sweep, a few boards) only give us title +
    # link - the stored description is a one-line stub, and the requirements
    # text ('3+ years experience') never reaches the filters or the local
    # scorer. The local scorer then scores the stub alone, which produced an
    # 81 for a senior role. A score is a claim about evidence: with no real
    # description, an UNVERIFIED score may not exceed 60. LLM-verified scores
    # (tier 1/2) are exempt - the LLM already judged with seniority awareness.
    STUB_CAP = 60
    def _is_stub_desc(j):
        d = j.get("description") or ""
        return len(d) < 140 or "See posting for details" in d

    # Tier-aware bar (recalibrated 2026-08-29 on n=464 LLM vs n=2896 local
    # scores): local tier-5 scores compress into 50-69 (median 57, inflated),
    # while LLM tier-1/2 scores are honest and spread 0-98 (median 35) - the
    # LLM demotes ~43% of local-approved jobs below 30. The same numeric bar
    # therefore means different things per tier: an honest LLM 48 is a better
    # bet than an unverified local 57. Give LLM-verified jobs a 10-point
    # lower bar; unverified jobs keep the configured bar until the daily
    # refinement pass verifies them.
    score_filtered = []
    pending_jobs = []
    for job in scored_jobs:
        match_obj = job.get("match") or {}
        is_pending = isinstance(match_obj, dict) and match_obj.get("match_grade") == "PENDING"
        if is_pending:
            pending_jobs.append(job)
            continue
        score = match_obj.get("score", 50) if isinstance(match_obj, dict) else 50
        is_llm_verified = isinstance(match_obj, dict) and match_obj.get("tier") in (1, 2)
        if not is_llm_verified and score > STUB_CAP and _is_stub_desc(job):
            # copy before capping - these dicts are live store objects
            job = dict(job)
            job["match"] = dict(match_obj)
            job["match"]["score"] = STUB_CAP
            job["match"]["evidence_capped"] = True
            score = STUB_CAP
        bar = max(0, min_score - 10) if is_llm_verified else min_score
        if score >= bar:
            score_filtered.append(job)

    # 10. EXCLUDE APPLIED JOBS (Phase 9: Applied jobs must not pollute feed)
    if applied_job_ids:
        feed_jobs = [j for j in score_filtered if j.get("id") not in applied_job_ids]
        pending_jobs = [j for j in pending_jobs if j.get("id") not in applied_job_ids]
    else:
        feed_jobs = score_filtered

    # 11. SORT
    sort_by = filters.get("sort_by", "match")
    if sort_by == "recent":
        feed_jobs.sort(key=lambda j: j.get("first_seen") or j.get("posted_date") or "", reverse=True)
    elif sort_by == "salary":
        feed_jobs.sort(key=lambda j: j.get("salary_min_inr", 0), reverse=True)
    else:
        feed_jobs.sort(key=lambda j: (j.get("match") or {}).get("score", 0), reverse=True)

    # Transparency: how many jobs each stage removed (for the UI's empty/feed
    # explanation — "never a black box").
    applied_excluded = (len(score_filtered) - len(feed_jobs)) if applied_job_ids else 0
    filter_breakdown = {
        "total_in_store": len(raw_jobs or []),
        "duplicates_merged": len(raw_jobs or []) - len(deduped_jobs),
        "dropped_invalid": len(deduped_jobs) - len(valid_jobs),
        "dropped_stale": len(valid_jobs) - len(recency_jobs),
        "dropped_role_mismatch": len(recency_jobs) - len(role_filtered),
        "dropped_location": len(role_filtered) - len(loc_filtered),
        "dropped_experience": len(loc_filtered) - len(exp_filtered),
        "dropped_seniority_keywords": len(exp_filtered) - len(exclusion_filtered),
        "awaiting_scoring": len(pending_jobs),
        "dropped_below_min_score": len(scored_jobs) - len(pending_jobs) - len(score_filtered),
        "already_applied": applied_excluded,
        "shown": len(feed_jobs),
    }

    return {
        "jobs": feed_jobs,
        "pending_jobs": pending_jobs,
        "filter_breakdown": filter_breakdown,
        "metrics": {
            "raw": len(raw_jobs),
            "deduped": len(deduped_jobs),
            "valid": len(valid_jobs),
            "filtered": len(feed_jobs),
            "pending": len(pending_jobs),
            "dedup_stats": dedup_metrics
        }
    }
