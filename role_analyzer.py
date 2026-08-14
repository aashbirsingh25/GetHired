from typing import Dict, Any, List
from application_tracker import ApplicationTracker

class RoleAnalyzer:
    def __init__(self, tracker: ApplicationTracker = None):
        self.tracker = tracker or ApplicationTracker()

    def analyze_role(self, job_title: str) -> Dict[str, Any]:
        all_apps = self.tracker.list_applications()
        query_lower = (job_title or "").lower()

        role_apps = [a for a in all_apps if query_lower in a.get("job_title", "").lower()]
        total = len(role_apps)

        if total == 0:
            return {
                "role": job_title,
                "total_applications": 0,
                "success_rate": 0.0,
                "interview_rate": 0.0,
                "avg_salary_inr": 0,
                "most_successful_company": "N/A",
                "recommendation": f"No applications for '{job_title}' logged yet."
            }

        offers = sum(1 for a in role_apps if a.get("status") in ["offer", "accepted"])
        interviews = sum(1 for a in role_apps if a.get("status") in ["interviewed", "offer", "accepted"])
        salaries = [a.get("salary_offered_inr") for a in role_apps if a.get("salary_offered_inr")]

        comp_offers = {}
        for a in role_apps:
            c = a.get("company", "Unknown")
            if a.get("status") in ["offer", "accepted"]:
                comp_offers[c] = comp_offers.get(c, 0) + 1

        top_comp = max(comp_offers, key=comp_offers.get) if comp_offers else (role_apps[0].get("company") if role_apps else "Google India")

        success_rate = round(offers / total, 2)
        interview_rate = round(interviews / total, 2)
        avg_salary = round(sum(salaries) / len(salaries)) if salaries else 900000

        rec = f"You have {int(success_rate*100)}% success rate for '{job_title}'. Focus on top companies like {top_comp}."

        return {
            "role": job_title,
            "total_applications": total,
            "success_rate": success_rate,
            "interview_rate": interview_rate,
            "avg_salary_inr": avg_salary,
            "most_successful_company": top_comp,
            "recommendation": rec
        }
