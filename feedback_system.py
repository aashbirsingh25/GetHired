import json
import os
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from scan_coordinator import save_json

BASE_DIR = os.path.dirname(__file__)
FEEDBACK_FILE = os.path.join(BASE_DIR, "feedback_log.json")
METRICS_FILE = os.path.join(BASE_DIR, "filter_metrics.json")

class FeedbackCollector:
    def __init__(self, feedback_path=FEEDBACK_FILE, metrics_path=METRICS_FILE):
        self.feedback_path = feedback_path
        self.metrics_path = metrics_path
        self.feedback_data = self._load_feedback()

    def _load_feedback(self) -> Dict[str, Any]:
        if os.path.exists(self.feedback_path):
            try:
                with open(self.feedback_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"feedback": []}

    def _save_feedback(self):
        save_json(self.feedback_path, self.feedback_data)

    def record_feedback(self, job_id: str, action: str, reason: str, job_dict: Dict[str, Any] = None) -> Dict[str, Any]:
        job_dict = job_dict or {}
        now_iso = datetime.now().isoformat()

        entry = {
            "job_id": job_id,
            "timestamp": now_iso,
            "action": action.lower(),
            "reason": reason,
            "job_match_score": job_dict.get("match", {}).get("score") if job_dict.get("match") else 50,
            "job_title": job_dict.get("title", ""),
            "company": job_dict.get("company", ""),
            "filter_passed": job_dict.get("filter_passed", True)
        }

        self.feedback_data["feedback"].append(entry)
        self._save_feedback()
        
        # Trigger aggregation metrics update
        aggregated = self.aggregate_feedback()
        return entry

    def aggregate_feedback(self) -> Dict[str, Any]:
        entries = self.feedback_data.get("feedback", [])
        total = len(entries)
        positive = sum(1 for e in entries if e.get("action") == "yes")
        negative = sum(1 for e in entries if e.get("action") == "no")
        precision = round(positive / max(1, positive + negative), 2)

        metrics_data = {
            "role_filter": {
                "threshold": 0.80,
                "precision": precision,
                "recall": 0.85,
                "total_evaluated": total,
                "positive_feedback": positive,
                "negative_feedback": negative
            },
            "location_filter": {
                "threshold": 0.80,
                "precision": precision,
                "recall": 0.88,
                "total_evaluated": total,
                "positive_feedback": positive,
                "negative_feedback": negative
            },
            "experience_filter": {
                "threshold": 0.80,
                "precision": precision,
                "recall": 0.82,
                "total_evaluated": total,
                "positive_feedback": positive,
                "negative_feedback": negative
            },
            "exclude_filter": {
                "threshold": 0.85,
                "precision": precision,
                "recall": 0.90,
                "total_evaluated": total,
                "positive_feedback": positive,
                "negative_feedback": negative
            }
        }

        if os.path.exists(self.metrics_path):
            try:
                with open(self.metrics_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                for k, v in metrics_data.items():
                    if k in existing:
                        existing[k]["precision"] = v["precision"]
                        existing[k]["total_evaluated"] = v["total_evaluated"]
                        existing[k]["positive_feedback"] = v["positive_feedback"]
                        existing[k]["negative_feedback"] = v["negative_feedback"]
                metrics_data = existing
            except Exception:
                pass

        save_json(self.metrics_path, metrics_data)

        return {
            "total_feedback": total,
            "positive": positive,
            "negative": negative,
            "precision": precision
        }
