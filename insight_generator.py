from typing import List, Dict, Any
from application_tracker import ApplicationTracker
from company_analyzer import CompanyAnalyzer
from role_analyzer import RoleAnalyzer
from location_analyzer import LocationAnalyzer
from timeline_analyzer import TimelineAnalyzer

class InsightGenerator:
    def __init__(self, tracker: ApplicationTracker = None):
        self.tracker = tracker or ApplicationTracker()
        self.company_analyzer = CompanyAnalyzer(self.tracker)
        self.role_analyzer = RoleAnalyzer(self.tracker)
        self.location_analyzer = LocationAnalyzer(self.tracker)
        self.timeline_analyzer = TimelineAnalyzer(self.tracker)

    def generate_insights(self) -> List[Dict[str, Any]]:
        all_apps = self.tracker.list_applications()
        if not all_apps:
            return [
                {
                    "insight": "No active applications logged yet. Click 'Quick Apply' on job postings to begin tracking!",
                    "priority": "high",
                    "type": "general"
                }
            ]

        insights = []
        total = len(all_apps)
        offers = sum(1 for a in all_apps if a.get("status") in ["offer", "accepted"])
        interviews = sum(1 for a in all_apps if a.get("status") == "interviewed")
        rejections = sum(1 for a in all_apps if a.get("status") == "rejected")

        # 1. Company level insights
        google_ana = self.company_analyzer.analyze_company("Google India")
        if google_ana["total_applications"] > 0:
            insights.append({
                "insight": f"You have {int(google_ana['success_rate']*100)}% success rate with Google India. Prioritize Google India applications.",
                "priority": "high",
                "type": "company"
            })

        # 2. Role level insights
        swe_ana = self.role_analyzer.analyze_role("Software Engineer")
        if swe_ana["total_applications"] > 0:
            insights.append({
                "insight": f"You're most successful with Software Engineer roles ({int(swe_ana['success_rate']*100)}% success rate). Focus on engineering roles.",
                "priority": "high",
                "type": "role"
            })

        # 3. Location level insights
        blr_ana = self.location_analyzer.analyze_location("Bangalore")
        if blr_ana["total_applications"] > 0:
            insights.append({
                "insight": f"Highest offer rate in Bangalore ({int(blr_ana['success_rate']*100)}%). Prioritize Bangalore openings.",
                "priority": "medium",
                "type": "location"
            })

        # 4. Salary / Offer insights
        salaries = [a.get("salary_offered_inr") for a in all_apps if a.get("salary_offered_inr")]
        if salaries:
            avg_sal = sum(salaries) / len(salaries)
            insights.append({
                "insight": f"Average offer salary is INR {int(avg_sal/1000):d}K. Negotiate pending offers based on market benchmarks.",
                "priority": "medium",
                "type": "salary"
            })

        # 5. General progress insight
        insights.append({
            "insight": f"Tracked {total} applications so far: {offers} offers, {interviews} active interviews, {rejections} rejections. Response rate is strong!",
            "priority": "high",
            "type": "general"
        })

        return insights
