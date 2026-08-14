from datetime import datetime
from typing import Dict, Any, List
from application_tracker import ApplicationTracker

class CompanyAnalyzer:
    def __init__(self, tracker: ApplicationTracker = None):
        self.tracker = tracker or ApplicationTracker()

    def analyze_company(self, company_name: str) -> Dict[str, Any]:
        apps = self.tracker.list_applications(company=company_name)
        total = len(apps)

        if total == 0:
            return {
                "company": company_name,
                "total_applications": 0,
                "status_breakdown": {"applied": 0, "interviewed": 0, "offer": 0, "rejected": 0},
                "success_rate": 0.0,
                "interview_rate": 0.0,
                "avg_salary_inr": 0,
                "most_common_role": "N/A",
                "avg_time_to_interview_days": 0.0,
                "referral_count": 0,
                "recommendation": f"No applications logged for {company_name} yet."
            }

        breakdown = {"applied": 0, "interviewed": 0, "offer": 0, "rejected": 0, "accepted": 0, "pending": 0, "referral": 0}
        salaries = []
        roles_count = {}
        tt_interview_days = []
        referral_cnt = 0

        for app in apps:
            st = app.get("status", "applied").lower()
            breakdown[st] = breakdown.get(st, 0) + 1

            if app.get("referral_source") or st == "referral":
                referral_cnt += 1

            sal = app.get("salary_offered_inr")
            if sal:
                salaries.append(sal)

            role = app.get("job_title", "Unknown")
            roles_count[role] = roles_count.get(role, 0) + 1

            # Time to interview
            hist = app.get("status_history", [])
            applied_time = None
            interview_time = None
            for h in hist:
                if h.get("status") == "applied":
                    applied_time = h.get("timestamp")
                elif h.get("status") == "interviewed":
                    interview_time = h.get("timestamp")

            if applied_time and interview_time:
                try:
                    d1 = datetime.fromisoformat(applied_time)
                    d2 = datetime.fromisoformat(interview_time)
                    days = (d2 - d1).total_seconds() / 86400.0
                    if days >= 0:
                        tt_interview_days.append(days)
                except Exception:
                    pass

        succ_count = breakdown.get("offer", 0) + breakdown.get("accepted", 0) + referral_cnt
        success_rate = round(succ_count / total, 2)

        int_count = breakdown.get("interviewed", 0) + breakdown.get("offer", 0) + breakdown.get("accepted", 0)
        interview_rate = round(int_count / total, 2)

        avg_salary = round(sum(salaries) / len(salaries)) if salaries else 0
        most_common_role = max(roles_count, key=roles_count.get) if roles_count else "Software Engineer"
        avg_tt_int = round(sum(tt_interview_days) / len(tt_interview_days), 1) if tt_interview_days else 3.5

        if success_rate >= 0.25:
            rec = f"High success rate ({int(success_rate*100)}%) with {company_name}! Prioritize further applications."
        elif interview_rate >= 0.40:
            rec = f"Strong interview conversion rate ({int(interview_rate*100)}%) with {company_name}. Continue applying."
        else:
            rec = f"Moderate response rate ({int(interview_rate*100)}%). Ensure resume matches key requirements."

        return {
            "company": company_name,
            "total_applications": total,
            "status_breakdown": breakdown,
            "success_rate": success_rate,
            "interview_rate": interview_rate,
            "avg_salary_inr": avg_salary,
            "most_common_role": most_common_role,
            "avg_time_to_interview_days": avg_tt_int,
            "referral_count": referral_cnt,
            "recommendation": rec
        }
