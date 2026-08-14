import json
import os
import hashlib
from datetime import datetime
from typing import Dict, Any, List, Optional
from status_manager import validate_transition

BASE_DIR = os.path.dirname(__file__)
APPLICATIONS_FILE = os.path.join(BASE_DIR, "applications.json")

class ApplicationTracker:
    def __init__(self, filepath=APPLICATIONS_FILE):
        self.filepath = filepath
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"applications": []}

    def _save(self):
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)

    def create_application(self, job_id: str, company: str, job_title: str, location: str,
                           applied_date: str = None, application_url: str = None,
                           match_score: int = 50) -> Dict[str, Any]:
        now_iso = datetime.now().isoformat()
        applied_at = applied_date or now_iso

        # Check existing application for same job_id
        apps = self.data.get("applications", [])
        for app in apps:
            if app.get("job_id") == job_id and app.get("status") != "archived":
                return app

        app_count = len(apps) + 1
        app_id = f"app-{app_count:03d}"

        new_app = {
            "id": app_id,
            "job_id": job_id,
            "company": company,
            "job_title": job_title,
            "location": location,
            "applied_date": applied_at,
            "application_url": application_url or "",
            "status": "applied",
            "status_history": [
                {"status": "applied", "timestamp": applied_at}
            ],
            "notes": "",
            "match_score": match_score or 50,
            "salary_offered_inr": None,
            "referral_source": None,
            "rejection_reason": None,
            "referral_received": False,
            "was_shortlisted": False,
            "interview_date": None,
            "tags": ["interested"]
        }

        apps.append(new_app)
        self.data["applications"] = apps
        self._save()
        return new_app

    def update_status(self, app_id: str, new_status: str = None, note: str = None,
                      tags: List[str] = None, salary_inr: float = None,
                      rejection_reason: str = None, referral_received: bool = None,
                      was_shortlisted: bool = None, interview_date: str = None) -> Optional[Dict[str, Any]]:
        apps = self.data.get("applications", [])
        now_iso = datetime.now().isoformat()

        for app in apps:
            if app.get("id") == app_id:
                curr_status = app.get("status", "applied")
                if new_status and not validate_transition(curr_status, new_status):
                    raise ValueError(f"Invalid status transition from '{curr_status}' to '{new_status}'")

                if new_status and curr_status != new_status:
                    app["status"] = new_status
                    app.setdefault("status_history", []).append({
                        "status": new_status,
                        "timestamp": now_iso
                    })

                if note is not None:
                    app["notes"] = note
                if tags is not None:
                    app["tags"] = tags
                if salary_inr is not None:
                    app["salary_offered_inr"] = salary_inr
                if rejection_reason is not None:
                    app["rejection_reason"] = rejection_reason
                if referral_received is not None:
                    app["referral_received"] = bool(referral_received)
                if was_shortlisted is not None:
                    app["was_shortlisted"] = bool(was_shortlisted)
                if interview_date is not None:
                    app["interview_date"] = interview_date

                self._save()
                return app
        return None

    def get_application(self, app_id: str) -> Optional[Dict[str, Any]]:
        for app in self.data.get("applications", []):
            if app.get("id") == app_id:
                return app
        return None

    def list_applications(self, status: str = None, company: str = None, location: str = None) -> List[Dict[str, Any]]:
        apps = self.data.get("applications", [])
        result = []
        for app in apps:
            if status and app.get("status", "").lower() != status.lower():
                continue
            if company and company.lower() not in app.get("company", "").lower():
                continue
            if location and location.lower() not in app.get("location", "").lower():
                continue
            result.append(app)
        return result

    def archive_application(self, app_id: str) -> Optional[Dict[str, Any]]:
        return self.update_status(app_id, "archived")

    def reconcile_legacy_applications(self, jobs_list: List[Dict[str, Any]]) -> Dict[str, int]:
        apps = self.data.get("applications", [])
        job_by_id = {j["id"]: j for j in jobs_list if j.get("id")}

        migrated = 0
        unmatched = 0
        ambiguous = 0

        for a in apps:
            jid = a.get("job_id")
            if jid in job_by_id:
                migrated += 1
                continue

            url = (a.get("application_url") or a.get("url") or "").strip()
            comp = (a.get("company") or "").strip().lower()
            title = (a.get("job_title") or a.get("title") or "").strip().lower()

            url_matches = [j for j in jobs_list if j.get("url") and j.get("url").strip() == url] if url else []
            if len(url_matches) == 1:
                a["job_id"] = url_matches[0]["id"]
                a["reconciled_from"] = jid
                migrated += 1
                continue
            elif len(url_matches) > 1:
                ambiguous += 1
                continue

            ctl_matches = [
                j for j in jobs_list
                if (j.get("company") or "").strip().lower() == comp
                and (j.get("title") or "").strip().lower() == title
            ]
            if len(ctl_matches) == 1:
                a["job_id"] = ctl_matches[0]["id"]
                a["reconciled_from"] = jid
                migrated += 1
            elif len(ctl_matches) > 1:
                ambiguous += 1
            else:
                unmatched += 1

        self._save()
        return {"migrated": migrated, "unmatched": unmatched, "ambiguous": ambiguous}

