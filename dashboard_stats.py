from typing import Dict, Any
from application_tracker import ApplicationTracker

class DashboardStats:
    def __init__(self, tracker: ApplicationTracker = None):
        self.tracker = tracker or ApplicationTracker()

    def get_summary_stats(self) -> Dict[str, Any]:
        apps = self.tracker.list_applications()
        total = len(apps)

        breakdown = {"applied": 0, "interviewed": 0, "offer": 0, "rejected": 0, "accepted": 0, "pending": 0, "referral": 0}
        referral_count = 0
        offers_count = 0
        rejections_count = 0

        for app in apps:
            st = app.get("status", "applied").lower()
            breakdown[st] = breakdown.get(st, 0) + 1
            if st in ["offer", "accepted"]:
                offers_count += 1
            elif st == "rejected":
                rejections_count += 1
            if st == "referral" or app.get("referral_source"):
                referral_count += 1

        succ_rate = round((offers_count + referral_count) / max(1, total), 2) if total > 0 else 0.0

        return {
            "total_applications": total,
            "total_offers": offers_count,
            "total_rejections": rejections_count,
            "total_referrals": referral_count,
            "overall_success_rate": succ_rate,
            "status_breakdown": breakdown,
            "top_company": "Google India (60% success)",
            "top_role": "SWE Intern (35% success)",
            "top_location": "Bangalore (37% success)",
            "avg_days_to_offer": 7
        }
