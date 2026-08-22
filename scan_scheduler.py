"""Active/dormant company scan tiering.

Classifies companies by historical yield so productive companies are
scanned every cycle while chronically empty ones are checked only every
Nth cycle. Deliberately simple and transparent:

- A company is DORMANT when its last `dormant_after_failures` scans all
  yielded zero jobs AND its last successful scan (if any) is older than
  `dormant_after_days` days.
- DORMANT companies are still scanned every `dormant_every_n_cycles`-th
  cycle, so a company that starts hiring again is promoted back within a
  few cycles (its next successful scan resets the streak).
- Companies with no scan history are ACTIVE (never seen = worth a look).

State lives in company_metrics.json (per-company zero-yield streak is
derived from total/successful scan counters kept by ScanCoordinator; the
global cycle counter is kept under the "scheduler" key).
"""
import os
from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULTS = {
    "enabled": True,
    "dormant_after_failures": 3,   # consecutive zero-yield scans
    "dormant_after_days": 7,       # and no success within this window
    "dormant_every_n_cycles": 4,   # dormant companies scan every 4th cycle
}


def get_tiering_config(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = dict(DEFAULTS)
    cfg.update((config or {}).get("scan_tiering", {}))
    return cfg


def classify_company(metrics: Dict[str, Any], cfg: Dict[str, Any], now: datetime = None) -> str:
    """Return 'active' or 'dormant' for a company's metrics entry."""
    if not metrics:
        return "active"  # never scanned: worth a look
    now = now or datetime.now()

    streak = int(metrics.get("zero_yield_streak", 0))
    if streak < int(cfg["dormant_after_failures"]):
        return "active"

    last_success = metrics.get("last_success_at")
    if last_success:
        try:
            success_dt = datetime.fromisoformat(last_success)
            if now - success_dt < timedelta(days=int(cfg["dormant_after_days"])):
                return "active"
        except (ValueError, TypeError):
            pass
    return "dormant"


def partition_companies(
    companies: List[Dict[str, Any]],
    metrics_store: Dict[str, Any],
    config: Dict[str, Any],
    cycle_number: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split companies into (to_scan, skipped_dormant) for this cycle."""
    cfg = get_tiering_config(config)
    if not cfg.get("enabled", True):
        return companies, []

    every_n = max(1, int(cfg["dormant_every_n_cycles"]))
    scan_dormant_this_cycle = (cycle_number % every_n == 0)

    to_scan, skipped = [], []
    comp_metrics = (metrics_store or {}).get("companies", {})
    for c in companies:
        tier = classify_company(comp_metrics.get(c.get("id")), cfg)
        if tier == "active" or scan_dormant_this_cycle:
            to_scan.append(c)
        else:
            skipped.append(c)
    return to_scan, skipped


def update_yield_streak(metrics_entry: Dict[str, Any], jobs_found: int, now_iso: str) -> None:
    """Maintain the per-company zero-yield streak and last-success stamp.

    Call after every scan of a company. Mutates metrics_entry in place.
    """
    if jobs_found > 0:
        metrics_entry["zero_yield_streak"] = 0
        metrics_entry["last_success_at"] = now_iso
    else:
        metrics_entry["zero_yield_streak"] = int(metrics_entry.get("zero_yield_streak", 0)) + 1


def next_cycle_number(metrics_store: Dict[str, Any]) -> int:
    """Increment and return the global scan cycle counter."""
    sched = metrics_store.setdefault("scheduler", {})
    sched["cycle_number"] = int(sched.get("cycle_number", 0)) + 1
    return sched["cycle_number"]
