from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Tuple, Union

def filter_by_recency(jobs: List[Dict[str, Any]], days: float = None, max_hours: float = None, hours: float = None) -> List[Dict[str, Any]]:
    """
    Filters jobs by recency window in hours or days. Returns list of jobs within cutoff.
    """
    h = max_hours or hours or (days * 24.0 if days else 168.0)
    now_dt = datetime.now(timezone.utc)

    recent_jobs = []
    for job in jobs:
        ts_str = job.get("first_seen") or job.get("posted_date") or job.get("scan_timestamp") or job.get("first_seen_at")
        is_recent = True

        if ts_str:
            try:
                dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                if not dt.tzinfo:
                    dt = dt.replace(tzinfo=timezone.utc)
                if (now_dt - dt).total_seconds() > h * 3600:
                    is_recent = False
            except Exception:
                is_recent = True

        if is_recent:
            job["recency"] = "recent"
            recent_jobs.append(job)

    return recent_jobs

def expand_search_if_sparse(jobs: List[Dict[str, Any]], filters: Dict[str, Any], min_results: int = 10) -> Dict[str, Any]:
    """
    Expands search timeframe stepwise (0.5h -> 1h -> 2h -> 6h -> 24h -> 72h)
    until min_results jobs are retained or max 72 hours is reached.
    """
    initial_hours = float(filters.get("upload_time_hours", 24))
    timeframe_steps = [0.5, 1.0, 2.0, 6.0, 24.0, 72.0]
    
    steps_to_try = [s for s in timeframe_steps if s >= initial_hours]
    if not steps_to_try:
        steps_to_try = [initial_hours, 72.0]

    now_dt = datetime.now(timezone.utc)

    def filter_for_hours(h: float) -> List[Dict[str, Any]]:
        retained = []
        for j in jobs:
            first_seen_str = j.get("first_seen") or j.get("posted_date") or j.get("scan_timestamp")
            if first_seen_str:
                try:
                    dt = datetime.fromisoformat(str(first_seen_str).replace("Z", "+00:00"))
                    if not dt.tzinfo:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if (now_dt - dt).total_seconds() > h * 3600:
                        continue
                except Exception:
                    pass
            retained.append(j)
        return retained

    timeframe_used = steps_to_try[0]
    filtered_jobs = filter_for_hours(timeframe_used)

    was_expanded = False
    for step in steps_to_try:
        if len(filtered_jobs) >= min_results:
            break
        if step > timeframe_used:
            timeframe_used = step
            filtered_jobs = filter_for_hours(timeframe_used)
            was_expanded = True

    return {
        "jobs": filtered_jobs,
        "timeframe_used_hours": timeframe_used,
        "was_expanded": was_expanded
    }
