import os
import json
import time
from typing import Dict, Any

try:
    import psutil
except ImportError:
    psutil = None

BASE_DIR = os.path.dirname(__file__)
LOG_FILE = os.path.join(BASE_DIR, "concurrency_log.json")

def load_json(filepath, default):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

class AdaptiveConcurrencyManager:
    def __init__(self, min_concurrent: int = 3, max_concurrent: int = 15, target_ram_percent: float = 70.0):
        self.min_concurrent = min_concurrent
        self.max_concurrent = max_concurrent
        self.target_ram_percent = target_ram_percent
        self.current_concurrency = min_concurrent
        self.batch_count = 0

    def get_current_ram_percent(self) -> float:
        if psutil is not None:
            try:
                return psutil.virtual_memory().percent
            except Exception:
                pass
        return 45.0  # Default nominal RAM percentage if psutil unavailable

    def get_next_batch_size(self) -> int:
        ram = self.get_current_ram_percent()
        old_concurrency = self.current_concurrency

        if ram > 85.0:
            self.current_concurrency = self.min_concurrent
            reason = f"Emergency drop to {self.min_concurrent} (RAM > 85%: {ram:.1f}%)"
            print(f"[AdaptiveConcurrencyManager] WARNING: {reason}")
        elif ram > 70.0:
            self.current_concurrency = max(self.min_concurrent, self.current_concurrency - 3)
            reason = f"Decreased to {self.current_concurrency} (RAM > 70%: {ram:.1f}%)"
        elif ram < 50.0:
            self.current_concurrency = min(self.max_concurrent, self.current_concurrency + 2)
            reason = f"Increased to {self.current_concurrency} (RAM < 50%: {ram:.1f}%)"
        else:
            reason = f"Maintained at {self.current_concurrency} (RAM nominal: {ram:.1f}%)"

        return self.current_concurrency

    def log_batch_stats(self, batch_size: int, ram_before: float, ram_after: float, duration_seconds: float, adjustment: str = ""):
        self.batch_count += 1
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        log_entry = {
            "batch": self.batch_count,
            "timestamp": now_iso,
            "concurrent_browsers": batch_size,
            "ram_before": round(ram_before, 1),
            "ram_after": round(ram_after, 1),
            "duration_s": round(duration_seconds, 2),
            "adjustment": adjustment or f"Active concurrency: {batch_size}"
        }

        logs_data = load_json(LOG_FILE, {"batches": []})
        batches = logs_data.get("batches", [])
        batches.append(log_entry)
        logs_data["batches"] = batches[-50:]
        save_json(LOG_FILE, logs_data)

        print(f"[AdaptiveConcurrencyManager] Batch #{self.batch_count}: {batch_size} contexts | RAM {ram_before:.1f}% -> {ram_after:.1f}% | Duration: {duration_seconds:.1f}s")
