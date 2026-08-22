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

def _matches_location(target_loc: str, location: str, description: str = "") -> bool:
    if not target_loc:
        return True
    loc_clean = target_loc.lower().strip()
    l_lower = (location or "").lower()
    d_lower = (description or "").lower()

    if loc_clean == "remote":
        return "remote" in l_lower or bool(re.search(r"\bremote\b", d_lower))

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
    filters = custom_filters or {}
    deduplicator = JobDeduplicator()
    
    # 1. NORMALIZE & DEDUPLICATE
    deduped_jobs, dedup_metrics = deduplicator.deduplicate(raw_jobs)
    
    # 2. REMOVE INVALID / CLOSED JOBS
    valid_jobs = []
    for job in deduped_jobs:
        if job.get("closed"):
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
    exp_filtered = loc_filtered

    # 7. EXCLUSION FILTER
    raw_excludes = filters.get("exclude_keywords")
    if raw_excludes is None:
        raw_excludes = ["Senior", "Lead", "Manager", "Principal", "Director", "Architect"]
    exclude_kws = _extract_filter_strings(raw_excludes)
    exclusion_filtered = []
    for job in exp_filtered:
        t_lower = (job.get("title") or "").lower()
        if any(re.search(r"\b" + re.escape(x.lower()) + r"\b", t_lower) for x in exclude_kws):
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
    score_filtered = []
    pending_jobs = []
    for job in scored_jobs:
        match_obj = job.get("match") or {}
        is_pending = isinstance(match_obj, dict) and match_obj.get("match_grade") == "PENDING"
        if is_pending:
            pending_jobs.append(job)
            continue
        score = match_obj.get("score", 50) if isinstance(match_obj, dict) else 50
        if score >= min_score:
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

    return {
        "jobs": feed_jobs,
        "pending_jobs": pending_jobs,
        "metrics": {
            "raw": len(raw_jobs),
            "deduped": len(deduped_jobs),
            "valid": len(valid_jobs),
            "filtered": len(feed_jobs),
            "pending": len(pending_jobs),
            "dedup_stats": dedup_metrics
        }
    }
