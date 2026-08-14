import re
from typing import List, Dict, Any, Set
from job_deduplicator import JobDeduplicator
from store_integrity_checker import check_job_posting_validity
from recency_filter import filter_by_recency
from hybrid_scorer import HybridJobScorer

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
        t_lower = (job.get("title") or "").lower()
        if any(r.lower() in t_lower for r in target_roles):
            role_filtered.append(job)

    # 5. LOCATION FILTER
    raw_locs = filters.get("target_location") or filters.get("locations") or []
    target_locs = _extract_filter_strings(raw_locs, "location")

    loc_filtered = []
    for job in role_filtered:
        if not target_locs:
            loc_filtered.append(job)
            continue
        l_lower = (job.get("location") or "").lower()
        d_lower = (job.get("description") or "").lower()
        if any(loc.lower() in l_lower or loc.lower() in d_lower for loc in target_locs):
            loc_filtered.append(job)

    # 6. EXPERIENCE FILTER
    exp_filtered = loc_filtered

    # 7. EXCLUSION FILTER
    raw_excludes = filters.get("exclude_keywords") or ["Senior", "Lead", "Manager", "Principal", "Director", "Architect"]
    exclude_kws = _extract_filter_strings(raw_excludes)
    exclusion_filtered = []
    for job in exp_filtered:
        t_lower = (job.get("title") or "").lower()
        if any(re.search(r"\b" + re.escape(x.lower()) + r"\b", t_lower) for x in exclude_kws):
            continue
        exclusion_filtered.append(job)

    # 8. CURRENT RESUME SCORING (Deterministic personalized scoring for all candidates)
    scored_jobs = []
    if resume_data and resume_data.get("has_resume"):
        scorer = HybridJobScorer(resume_data)

        for job in exclusion_filtered:
            if not job.get("match") or job["match"].get("resume_version_hash") != scorer.resume_hash:
                job["match"] = scorer.score_job(job)
            scored_jobs.append(job)
    else:
        scored_jobs = exclusion_filtered


    # 9. MINIMUM MATCH SCORE
    min_score = filters.get("min_match_score", 0)
    score_filtered = []
    for job in scored_jobs:
        score = job.get("match", {}).get("score", 50) if job.get("match") else 50
        if score >= min_score:
            score_filtered.append(job)

    # 10. EXCLUDE APPLIED JOBS (Phase 9: Applied jobs must not pollute feed)
    if applied_job_ids:
        feed_jobs = [j for j in score_filtered if j.get("id") not in applied_job_ids]
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
        "metrics": {
            "raw": len(raw_jobs),
            "deduped": len(deduped_jobs),
            "valid": len(valid_jobs),
            "filtered": len(feed_jobs),
            "dedup_stats": dedup_metrics
        }
    }
