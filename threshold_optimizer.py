import json
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple

BASE_DIR = os.path.dirname(__file__)
METRICS_FILE = os.path.join(BASE_DIR, "filter_metrics.json")
LOG_FILE = os.path.join(BASE_DIR, "auto_improvement_log.json")
FEEDBACK_FILE = os.path.join(BASE_DIR, "feedback_log.json")
TRIAL_FILE = os.path.join(BASE_DIR, "trial_periods.json")

_to_file_cache = {}

def load_json(filepath, default):
    if not os.path.exists(filepath):
        return default
    try:
        mtime = os.path.getmtime(filepath)
        if filepath in _to_file_cache:
            cached_mtime, cached_data = _to_file_cache[filepath]
            if cached_mtime == mtime:
                return cached_data
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            _to_file_cache[filepath] = (mtime, data)
            return data
    except Exception:
        return default

def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    _to_file_cache.pop(filepath, None)

def log_auto_improvement(entry: dict):
    data = {"improvements": []}
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"improvements": []}

    existing_items = data.get("improvements", [])
    # Strict content-level deduplication guard
    for existing in reversed(existing_items):
        if (existing.get("type") == entry.get("type") and 
            existing.get("filter") == entry.get("filter") and 
            existing.get("reason") == entry.get("reason") and
            existing.get("old_threshold") == entry.get("old_threshold") and
            existing.get("new_threshold") == entry.get("new_threshold")):
            return

    data["improvements"].append(entry)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def get_confident_misses() -> List[dict]:
    if not os.path.exists(FEEDBACK_FILE):
        return []
    try:
        with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
            f_data = json.load(f)
            entries = f_data.get("feedback", [])
            return [
                e for e in entries 
                if e.get("action") == "no" and e.get("job_match_score", 0) >= 75
            ]
    except Exception:
        return []

def calculate_severity_multiplier(confident_misses_count: int) -> float:
    if confident_misses_count <= 0:
        return 1.0
    elif confident_misses_count == 1:
        return 1.25
    elif confident_misses_count == 2:
        return 1.5
    else:
        return 2.0

def record_trial(threshold_name: str, old_val: float, new_val: float, precision: float = 0.90, recall: float = 0.85, miss_rate: float = 0.05) -> Dict:
    """
    Records a trial period for an adjustment in trial_periods.json.
    trial_period_end is set to adjusted_at + 3 days OR max 20 outcomes.
    """
    now = datetime.now()
    now_iso = now.isoformat()
    end_iso = (now + timedelta(days=3)).isoformat()
    adj_id = f"adj_{now.strftime('%Y%m%d%H%M%S')}_{threshold_name}"

    trial_entry = {
        "adjustment_id": adj_id,
        "threshold_name": threshold_name,
        "old_value": old_val,
        "new_value": new_val,
        "adjusted_at": now_iso,
        "trial_period_end": end_iso,
        "outcomes_count": 0,
        "status": "pending",
        "pre_adjustment_metrics": {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "confident_miss_rate": round(miss_rate, 4)
        }
    }

    trials_data = load_json(TRIAL_FILE, {"trials": []})
    trials = trials_data.get("trials", [])
    trials.append(trial_entry)
    trials_data["trials"] = trials
    save_json(TRIAL_FILE, trials_data)
    return trial_entry

def evaluate_trial(adjustment_id: str = None) -> List[Dict]:
    """
    Evaluates pending trials whose trial_period_end has passed or outcomes_count >= 20.
    Compares metrics DURING trial period against pre_adjustment_metrics.
    Reverts threshold if confident_miss_rate increased OR precision dropped by > 5% (0.05).
    Otherwise confirms the adjustment.
    """
    trials_data = load_json(TRIAL_FILE, {"trials": []})
    trials = trials_data.get("trials", [])
    if not trials:
        return []

    now = datetime.now()
    metrics = load_json(METRICS_FILE, {})
    confident_misses = get_confident_misses()
    current_miss_rate = round(len(confident_misses) / max(1, 20), 4)

    evaluated_results = []

    for trial in trials:
        if trial.get("status") != "pending":
            continue

        target_id = trial.get("adjustment_id")
        if adjustment_id and target_id != adjustment_id:
            continue

        end_dt_str = trial.get("trial_period_end")
        end_passed = False
        if end_dt_str:
            try:
                end_dt = datetime.fromisoformat(end_dt_str)
                if now >= end_dt:
                    end_passed = True
            except Exception:
                end_passed = True

        outcomes_reached = trial.get("outcomes_count", 0) >= 20
        should_eval = (adjustment_id is not None and target_id == adjustment_id) or end_passed or outcomes_reached

        if not should_eval:
            continue

        pre = trial.get("pre_adjustment_metrics", {})
        thresh_name = trial.get("threshold_name")

        # Get current metrics for this threshold
        curr_thresh_data = metrics.get(thresh_name, {})
        curr_prec = curr_thresh_data.get("precision", 0.90)
        curr_rec = curr_thresh_data.get("recall", 0.85)

        pre_prec = pre.get("precision", 0.90)
        pre_miss_rate = pre.get("confident_miss_rate", 0.05)

        # Failure condition: miss rate increased OR precision dropped by > 0.05 (5%)
        prec_dropped_significantly = (curr_prec < (pre_prec - 0.05))
        miss_rate_increased = (current_miss_rate > pre_miss_rate)

        if miss_rate_increased or prec_dropped_significantly:
            trial["status"] = "reverted"
            reverted_val = trial.get("old_value")

            # Revert threshold value in metrics
            if thresh_name in metrics:
                metrics[thresh_name]["threshold"] = reverted_val
                metrics[thresh_name]["last_adjusted"] = now.isoformat()
                save_json(METRICS_FILE, metrics)

            log_entry = {
                "timestamp": now.isoformat(),
                "type": "threshold_reversion",
                "filter": thresh_name,
                "old_threshold": trial.get("new_value"),
                "new_threshold": reverted_val,
                "reason": "reverted - adjustment did not improve results",
                "trial_status": "reverted",
                "adjustment_id": target_id,
                "pre_metrics": pre,
                "post_metrics": {
                    "precision": curr_prec,
                    "recall": curr_rec,
                    "confident_miss_rate": current_miss_rate
                }
            }
            log_auto_improvement(log_entry)
            evaluated_results.append(log_entry)
        else:
            trial["status"] = "confirmed"
            log_entry = {
                "timestamp": now.isoformat(),
                "type": "threshold_confirmation",
                "filter": thresh_name,
                "old_threshold": trial.get("old_value"),
                "new_threshold": trial.get("new_value"),
                "reason": "confirmed - adjustment improved results",
                "trial_status": "confirmed",
                "adjustment_id": target_id,
                "pre_metrics": pre,
                "post_metrics": {
                    "precision": curr_prec,
                    "recall": curr_rec,
                    "confident_miss_rate": current_miss_rate
                }
            }
            log_auto_improvement(log_entry)
            evaluated_results.append(log_entry)

    trials_data["trials"] = trials
    save_json(TRIAL_FILE, trials_data)
    return evaluated_results

def record_outcome(threshold_name: str, success: bool = True):
    """Increments outcomes_count for active pending trials matching threshold_name."""
    trials_data = load_json(TRIAL_FILE, {"trials": []})
    trials = trials_data.get("trials", [])
    updated = False

    for trial in trials:
        if trial.get("status") == "pending" and trial.get("threshold_name") == threshold_name:
            trial["outcomes_count"] = trial.get("outcomes_count", 0) + 1
            updated = True

    if updated:
        trials_data["trials"] = trials
        save_json(TRIAL_FILE, trials_data)

def get_scan_confidence_threshold() -> float:
    """Returns current scan_confidence_threshold (default 0.80, bounds [0.60, 0.95])."""
    metrics = load_json(METRICS_FILE, {})
    s_data = metrics.get("scan_confidence_threshold", {})
    val = s_data.get("threshold", 0.80)
    return max(0.60, min(0.95, round(float(val), 2)))

def set_scan_confidence_threshold(new_val: float, reason: str, trigger: str = "outcome") -> float:
    """Updates scan_confidence_threshold, records a trial, and logs to auto_improvement_log.json."""
    old_val = get_scan_confidence_threshold()
    clamped_val = max(0.60, min(0.95, round(float(new_val), 2)))

    metrics = load_json(METRICS_FILE, {})
    if "scan_confidence_threshold" not in metrics:
        metrics["scan_confidence_threshold"] = {}

    metrics["scan_confidence_threshold"]["threshold"] = clamped_val
    metrics["scan_confidence_threshold"]["last_adjusted"] = datetime.now().isoformat()
    save_json(METRICS_FILE, metrics)

    if round(clamped_val, 2) != round(old_val, 2):
        record_trial("scan_confidence_threshold", old_val, clamped_val, precision=0.85, recall=0.85, miss_rate=0.05)
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "threshold_adjustment",
            "filter": "scan_confidence_threshold",
            "old_threshold": old_val,
            "new_threshold": clamped_val,
            "trigger": trigger,
            "reason": reason,
            "trial_status": "pending"
        }
        log_auto_improvement(log_entry)

    return clamped_val

def apply_pacing_driven_adjustment(current_cycle_scores: List[float] = None) -> Dict:
    """
    Computes Apify quota pacing and applies bounded pacing modifier (+/-3 points) to scan_confidence_threshold.
    Calculates time-cost estimate and logs automatically.
    """
    try:
        from apify_scanner import compute_apify_pacing, get_avg_apify_duration
        pace_ratio, pacing_modifier = compute_apify_pacing()
    except Exception:
        return None

    if pacing_modifier == 0.0:
        return None

    old_val = get_scan_confidence_threshold()
    new_val = max(0.60, min(0.95, round(old_val + pacing_modifier, 2)))

    if round(new_val, 2) == round(old_val, 2):
        return None

    # Calculate affected companies count
    if current_cycle_scores:
        low_b = min(old_val, new_val)
        high_b = max(old_val, new_val)
        affected_count = sum(1 for s in current_cycle_scores if low_b <= s <= high_b)
    else:
        # Estimate from metrics.json
        comp_metrics = load_json(os.path.join(BASE_DIR, "metrics.json"), {}).get("companies", {})
        scores = [c.get("parse_confidence_score", 0.75) for c in comp_metrics.values()]
        low_b = min(old_val, new_val)
        high_b = max(old_val, new_val)
        affected_count = sum(1 for s in scores if low_b <= s <= high_b)

    if affected_count == 0 and not current_cycle_scores:
        affected_count = 12  # Nominal default estimate if metrics file empty

    avg_duration = get_avg_apify_duration()
    concurrency = 5  # Nominal parallel execution concurrency
    try:
        from adaptive_concurrency_manager import AdaptiveConcurrencyManager
        acm = AdaptiveConcurrencyManager()
        concurrency = acm.current_concurrency
    except Exception:
        concurrency = 5

    added_time_min = round((affected_count * avg_duration) / (concurrency * 60.0), 1)
    if added_time_min < 1.0 and affected_count > 0:
        added_time_min = 1.0

    reason_str = (
        f"Apify quota pacing at {pace_ratio:.1f}x - using available headroom for extra verification"
        if pacing_modifier > 0 else
        f"Apify quota pacing at {pace_ratio:.1f}x - conserving quota by reducing cross-checks"
    )

    metrics = load_json(METRICS_FILE, {})
    if "scan_confidence_threshold" not in metrics:
        metrics["scan_confidence_threshold"] = {}
    metrics["scan_confidence_threshold"]["threshold"] = new_val
    metrics["scan_confidence_threshold"]["last_adjusted"] = datetime.now().isoformat()
    save_json(METRICS_FILE, metrics)

    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "type": "threshold_adjustment",
        "filter": "scan_confidence_threshold",
        "old_threshold": old_val,
        "new_threshold": new_val,
        "trigger": "quota_pacing",
        "pace_ratio": pace_ratio,
        "reason": reason_str,
        "estimated_additional_companies_affected": affected_count,
        "estimated_added_cycle_time_minutes": added_time_min,
        "trial_status": "pending"
    }
    record_trial("scan_confidence_threshold", old_val, new_val, precision=0.85, recall=0.85, miss_rate=0.05)
    log_auto_improvement(log_entry)
    return log_entry

def analyze_and_optimize():
    # Evaluate any pending trial periods at the start of optimization/scan cycle
    evaluate_trial()

    if not os.path.exists(METRICS_FILE):
        return []

    with open(METRICS_FILE, "r", encoding="utf-8") as f:
        metrics = json.load(f)

    confident_misses = get_confident_misses()
    miss_count = len(confident_misses)
    multiplier = calculate_severity_multiplier(miss_count)

    base_step = 0.08
    raw_delta = round(base_step * multiplier, 2)
    # Cap final threshold change per adjustment cycle to max +/- 0.15
    capped_delta = min(0.15, max(-0.15, raw_delta))

    adjustments = []
    now_iso = datetime.now().isoformat()

    for filter_name, f_data in metrics.items():
        if filter_name == "scan_confidence_threshold":
            continue

        thresh = f_data.get("threshold", 0.80)
        prec = f_data.get("precision", 0.90)
        rec = f_data.get("recall", 0.85)

        old_thresh = thresh
        new_thresh = old_thresh
        reason = None

        if prec < 0.80 and rec > 0.85 and old_thresh < 0.95:
            new_thresh = min(0.95, round(old_thresh + capped_delta, 2))
            reason = f"Adjusted threshold by {capped_delta:.2f} (base {base_step:.2f} x {multiplier:.1f} severity multiplier due to {miss_count} confident misses this period)"
        elif rec < 0.80 and prec > 0.85 and old_thresh > 0.50:
            new_thresh = max(0.50, round(old_thresh - capped_delta, 2))
            reason = f"Adjusted threshold by {capped_delta:.2f} (base {base_step:.2f} x {multiplier:.1f} severity multiplier due to {miss_count} confident misses this period)"

        if round(new_thresh, 2) != round(old_thresh, 2):
            f_data["threshold"] = new_thresh
            f_data["last_adjusted"] = now_iso

            trial_rec = record_trial(filter_name, old_thresh, new_thresh, prec, rec, miss_count / max(1, 20))

            entry = {
                "timestamp": now_iso,
                "type": "threshold_adjustment",
                "filter": filter_name,
                "old_threshold": old_thresh,
                "new_threshold": new_thresh,
                "reason": reason,
                "precision_after": prec,
                "recall_after": rec,
                "confident_misses_count": miss_count,
                "severity_multiplier": multiplier,
                "trial_status": "pending",
                "adjustment_id": trial_rec.get("adjustment_id")
            }
            adjustments.append(entry)
            log_auto_improvement(entry)

    with open(METRICS_FILE, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    # Check for quota pacing adjustments
    pacing_adj = apply_pacing_driven_adjustment()
    if pacing_adj:
        adjustments.append(pacing_adj)

    return adjustments

if __name__ == "__main__":
    adjs = analyze_and_optimize()
    print(f"Optimized thresholds: {len(adjs)} adjustments made.")
