"""Prune the jobs store: keep only the freshest jobs (max 3 days old).

User decision 2026-09-05: "don't keep jobs in database for a long time -
delete after max 3 days, I only want latest jobs." Fresher hiring moves
fast; a 3-day-old unapplied posting is usually already flooded.

NEVER deleted, regardless of age:
  - jobs referenced by the tracker (applied / saved / apply-later): they
    back the user's application history.

Side benefit: the store drops from ~17k rows to a few thousand, which makes
every scan, dedupe and scoring pass proportionally faster.
"""
import json
import os
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_FILE = os.path.join(BASE_DIR, "jobs_store.json")
MAX_AGE_HOURS = 72


def _job_age_hours(job, now):
    ts = job.get("posted_date") or job.get("first_seen") or job.get("first_seen_at") \
        or job.get("scan_timestamp") or job.get("last_seen")
    if not ts:
        return None  # unknown age
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now - dt).total_seconds() / 3600.0
    except Exception:
        return None


def _protected_ids():
    ids = set()
    try:
        apps = json.load(open(os.path.join(BASE_DIR, "applications.json"), encoding="utf-8"))
        for a in (apps.get("applications", apps) if isinstance(apps, dict) else apps):
            if isinstance(a, dict) and a.get("job_id"):
                ids.add(a["job_id"])
    except Exception:
        pass
    for fname, key in (("saved_jobs.json", "saved_jobs"), ("apply_later.json", "apply_later")):
        try:
            ids |= set(json.load(open(os.path.join(BASE_DIR, fname), encoding="utf-8")).get(key, []))
        except Exception:
            pass
    return ids


def prune_old_jobs(max_age_hours: float = MAX_AGE_HOURS) -> dict:
    """Remove jobs older than max_age_hours. Returns a small stats dict."""
    from scan_coordinator import save_json  # atomic writer
    try:
        data = json.load(open(JOBS_FILE, encoding="utf-8"))
    except Exception as e:
        return {"error": str(e)[:80]}
    jobs = data.get("jobs", [])
    now = datetime.now(timezone.utc)
    protected = _protected_ids()

    kept, dropped = [], 0
    for j in jobs:
        if j.get("id") in protected:
            kept.append(j)
            continue
        age = _job_age_hours(j, now)
        # unknown age: keep once - first_seen gets stamped on capture, so a
        # missing timestamp is an anomaly, not proof of staleness
        if age is not None and age > max_age_hours:
            dropped += 1
            continue
        kept.append(j)

    if dropped:
        data["jobs"] = kept
        save_json(JOBS_FILE, data)
    stats = {"before": len(jobs), "kept": len(kept), "dropped": dropped,
             "protected": len(protected)}
    print(f"[Prune] jobs store: {stats['before']} -> {stats['kept']} "
          f"(dropped {dropped} older than {max_age_hours:.0f}h; {len(protected)} tracked ids protected)")
    return stats
