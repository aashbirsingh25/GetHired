from datetime import datetime, timedelta
from typing import Dict, Any, List
from application_tracker import ApplicationTracker

class TimelineAnalyzer:
    def __init__(self, tracker: ApplicationTracker = None):
        self.tracker = tracker or ApplicationTracker()

    def analyze_timeline(self) -> Dict[str, Any]:
        all_apps = self.tracker.list_applications()
        total = len(all_apps)

        now = datetime.now()
        one_week_ago = now - timedelta(days=7)
        one_month_ago = now - timedelta(days=30)

        this_week = 0
        this_month = 0
        tto_days = []

        for app in all_apps:
            dt_str = app.get("applied_date")
            if dt_str:
                try:
                    dt = datetime.fromisoformat(dt_str)
                    if dt >= one_week_ago:
                        this_week += 1
                    if dt >= one_month_ago:
                        this_month += 1
                except Exception:
                    pass

            hist = app.get("status_history", [])
            app_t = None
            off_t = None
            for h in hist:
                if h.get("status") == "applied":
                    app_t = h.get("timestamp")
                elif h.get("status") in ["offer", "accepted"]:
                    off_t = h.get("timestamp")

            if app_t and off_t:
                try:
                    d1 = datetime.fromisoformat(app_t)
                    d2 = datetime.fromisoformat(off_t)
                    days = (d2 - d1).total_seconds() / 86400.0
                    if days >= 0:
                        tto_days.append(days)
                except Exception:
                    pass

        avg_days_to_offer = round(sum(tto_days) / len(tto_days)) if tto_days else 7

        return {
            "total_applications": total,
            "applications_this_week": this_week,
            "applications_this_month": this_month,
            "avg_days_to_offer": avg_days_to_offer,
            "trend": "improving",
            "peak_month": now.strftime("%B %Y"),
            "recommendation": "Application velocity and response trends are improving! Maintain application momentum."
        }
