import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List

BASE_DIR = os.path.dirname(__file__)
CYCLE_YIELD_FILE = os.path.join(BASE_DIR, "cycle_yield_history.json")

_yield_cache = {}

def load_json_cached(filepath, default):
    if not os.path.exists(filepath):
        return default
    try:
        mtime = os.path.getmtime(filepath)
        if filepath in _yield_cache:
            cached_mtime, cached_data = _yield_cache[filepath]
            if cached_mtime == mtime:
                return cached_data
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            _yield_cache[filepath] = (mtime, data)
            return data
    except Exception:
        return default

def load_json(filepath, default):
    return load_json_cached(filepath, default)

import time
import threading

def save_json(filepath, data, indent=2):
    dir_name = os.path.dirname(filepath) or "."
    os.makedirs(dir_name, exist_ok=True)
    tmp_path = f"{filepath}.tmp_{os.getpid()}_{threading.get_ident()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp_path, filepath)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02)
    except Exception as e:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        raise e
    _yield_cache.pop(filepath, None)

def get_hour_bucket_name(hour: int) -> str:
    start_h = (hour // 3) * 3
    end_h = start_h + 3
    return f"{start_h:02d}:00-{end_h:02d}:00"

class CycleYieldTracker:
    def __init__(self, filepath: str = CYCLE_YIELD_FILE):
        self.filepath = filepath

    def record_cycle_outcome(self, cycle_start_time: datetime, jobs_found: int, high_relevance_jobs_found: int) -> None:
        if cycle_start_time.tzinfo is None:
            cycle_start_time = cycle_start_time.replace(tzinfo=timezone.utc)

        hour = cycle_start_time.hour
        bucket = get_hour_bucket_name(hour)
        now_iso = cycle_start_time.isoformat()
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

        history = load_json(self.filepath, {"cycles": []})
        cycles = history.get("cycles", [])

        # Prune old cycles beyond 30 days
        valid_cycles = [
            c for c in cycles
            if c.get("timestamp", "9999") >= cutoff_date
        ]

        entry = {
            "bucket": bucket,
            "hour": hour,
            "jobs_found": jobs_found,
            "high_relevance_jobs_found": high_relevance_jobs_found,
            "timestamp": now_iso
        }
        valid_cycles.append(entry)
        history["cycles"] = valid_cycles
        save_json(self.filepath, history)

    def get_yield_multiplier(self, current_hour: int) -> float:
        history = load_json(self.filepath, {"cycles": []})
        cycles = history.get("cycles", [])

        if not cycles:
            return 1.0

        bucket = get_hour_bucket_name(current_hour)
        bucket_cycles = [c for c in cycles if c.get("bucket") == bucket]

        # Neutral 1.0 if insufficient history for this bucket (< 5 samples)
        if len(bucket_cycles) < 5:
            return 1.0

        all_yields = [c.get("jobs_found", 0) + 1.5 * c.get("high_relevance_jobs_found", 0) for c in cycles]
        overall_avg = sum(all_yields) / float(len(all_yields)) if all_yields else 0.0

        if overall_avg <= 0:
            return 1.0

        bucket_yields = [c.get("jobs_found", 0) + 1.5 * c.get("high_relevance_jobs_found", 0) for c in bucket_cycles]
        bucket_avg = sum(bucket_yields) / float(len(bucket_yields))

        multiplier = round(bucket_avg / overall_avg, 2)
        # Bounded between 0.3x and 2.5x
        return max(0.3, min(2.5, multiplier))

    def get_cycle_reasoning(self, current_hour: int) -> str:
        multiplier = self.get_yield_multiplier(current_hour)
        bucket = get_hour_bucket_name(current_hour)
        if multiplier < 0.8:
            note = f"Current cycle ({bucket} bucket) has historical yield multiplier {multiplier}x - running lean this cycle"
        elif multiplier > 1.2:
            note = f"Current cycle ({bucket} bucket) has historical yield multiplier {multiplier}x - allocating standard/high budget"
        else:
            note = f"Current cycle ({bucket} bucket) has historical yield multiplier {multiplier}x - allocating standard budget"
        return note

    def get_yield_summary(self) -> Dict:
        history = load_json(self.filepath, {"cycles": []})
        cycles = history.get("cycles", [])
        
        buckets_data = {}
        for b_idx in range(8):
            b_name = f"{b_idx*3:02d}:00-{(b_idx+1)*3:02d}:00"
            b_list = [c for c in cycles if c.get("bucket") == b_name]
            sample_cnt = len(b_list)
            avg_jobs = round(sum(c.get("jobs_found", 0) for c in b_list) / sample_cnt, 1) if sample_cnt > 0 else 0.0
            avg_high_rel = round(sum(c.get("high_relevance_jobs_found", 0) for c in b_list) / sample_cnt, 1) if sample_cnt > 0 else 0.0
            mult = self.get_yield_multiplier(b_idx * 3)

            buckets_data[b_name] = {
                "samples": sample_cnt,
                "avg_jobs_found": avg_jobs,
                "avg_high_relevance": avg_high_rel,
                "yield_multiplier": mult
            }
        return buckets_data
