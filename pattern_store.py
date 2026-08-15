import json
import os
import requests
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup

PATTERN_FILE = os.path.join(os.path.dirname(__file__), "pattern_store.json")
REVALIDATION_LOG_FILE = os.path.join(os.path.dirname(__file__), "pattern_revalidation_log.json")
COMPANIES_FILE = os.path.join(os.path.dirname(__file__), "companies.json")

def load_json(filepath, default):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

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
        os.replace(tmp_path, filepath)
    except Exception as e:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        raise e

class PatternStore:
    def __init__(self, filepath=PATTERN_FILE):
        self.filepath = filepath
        self.patterns = self._load()

    def _load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save(self):
        save_json(self.filepath, self.patterns)

    def get_pattern(self, company_id: str):
        comp = self.patterns.get(company_id, {})
        return comp.get("last_successful_pattern")

    def save_pattern(self, company_id: str, job_card_selector: str, title_selector: str, location_selector: str, apply_link_selector: str):
        existing = self.patterns.get(company_id, {}).get("last_successful_pattern", {})
        success_count = existing.get("success_count", 0) + 1
        failure_count = existing.get("failure_count", 0)

        pattern_data = {
            "job_card_selector": job_card_selector,
            "title_selector": title_selector,
            "location_selector": location_selector,
            "apply_link_selector": apply_link_selector,
            "success_count": success_count,
            "failure_count": failure_count,
            "last_used": datetime.now(timezone.utc).isoformat(),
            "last_validated": datetime.now(timezone.utc).isoformat(),
            "status": "active"
        }

        if company_id not in self.patterns:
            self.patterns[company_id] = {}
        self.patterns[company_id]["last_successful_pattern"] = pattern_data
        self._save()

    def record_failure(self, company_id: str):
        if company_id in self.patterns and "last_successful_pattern" in self.patterns[company_id]:
            self.patterns[company_id]["last_successful_pattern"]["failure_count"] += 1
            if self.patterns[company_id]["last_successful_pattern"]["failure_count"] >= 3:
                self.patterns[company_id]["last_successful_pattern"]["status"] = "stale"
                self.patterns[company_id]["last_successful_pattern"]["needs_relearning"] = True
            self._save()

    def scheduled_pattern_check(self, days_threshold: int = 7) -> dict:
        """
        Weekly background pattern revalidation.
        Checks stored patterns older than 7 days against live career pages.
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days_threshold)

        companies_data = load_json(COMPANIES_FILE, {"companies": []})
        comp_map = {c["id"]: c.get("career_url") for c in companies_data.get("companies", []) if c.get("id")}

        checked = 0
        validated = 0
        stale_flagged = 0

        for comp_id, comp_info in self.patterns.items():
            pattern = comp_info.get("last_successful_pattern")
            if not pattern:
                continue

            last_time_str = pattern.get("last_validated") or pattern.get("last_used")
            should_revalidate = True
            if last_time_str:
                try:
                    dt = datetime.fromisoformat(last_time_str.replace("Z", "+00:00"))
                    if dt > cutoff:
                        should_revalidate = False
                except Exception:
                    should_revalidate = True

            if not should_revalidate:
                continue

            checked += 1
            career_url = comp_map.get(comp_id)
            pattern_worked = False

            if career_url:
                try:
                    resp = requests.get(career_url, timeout=2.5, headers={"User-Agent": "Mozilla/5.0"})
                    if resp.status_code == 200:
                        soup = BeautifulSoup(resp.text, "html.parser")
                        card_sel = pattern.get("job_card_selector", "")
                        cards = soup.select(card_sel) if card_sel else []
                        if len(cards) > 0:
                            pattern_worked = True
                except Exception:
                    pattern_worked = False

            reval_logs = load_json(REVALIDATION_LOG_FILE, {"revalidations": []})
            logs_list = reval_logs.get("revalidations", [])

            if pattern_worked:
                pattern["last_validated"] = now.isoformat()
                pattern["status"] = "active"
                validated += 1
                logs_list.append({
                    "timestamp": now.isoformat(),
                    "company": comp_id,
                    "previous_pattern_worked": True,
                    "now_failing": False,
                    "action": "validated_active"
                })
            else:
                pattern["status"] = "stale"
                pattern["needs_relearning"] = True
                pattern["failure_count"] = pattern.get("failure_count", 0) + 1
                stale_flagged += 1
                logs_list.append({
                    "timestamp": now.isoformat(),
                    "company": comp_id,
                    "previous_pattern_worked": True,
                    "now_failing": True,
                    "action": "flagged_for_relearning"
                })

            reval_logs["revalidations"] = logs_list[-100:]
            save_json(REVALIDATION_LOG_FILE, reval_logs)

        self._save()

        summary = {
            "timestamp": now.isoformat(),
            "checked_count": checked,
            "validated_count": validated,
            "stale_flagged": stale_flagged
        }
        print(f"[PatternStore] Scheduled revalidation finished: {summary}")
        return summary
