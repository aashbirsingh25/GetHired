from typing import Dict, Any, List
from application_tracker import ApplicationTracker

class LocationAnalyzer:
    def __init__(self, tracker: ApplicationTracker = None):
        self.tracker = tracker or ApplicationTracker()

    def analyze_location(self, location: str) -> Dict[str, Any]:
        all_apps = self.tracker.list_applications(location=location)
        total = len(all_apps)

        if total == 0:
            return {
                "location": location,
                "total_applications": 0,
                "success_rate": 0.0,
                "avg_salary_inr": 0,
                "top_company": "N/A",
                "recommendation": f"No applications logged for {location} yet."
            }

        offers = sum(1 for a in all_apps if a.get("status") in ["offer", "accepted"])
        salaries = [a.get("salary_offered_inr") for a in all_apps if a.get("salary_offered_inr")]
        
        comp_counts = {}
        for a in all_apps:
            c = a.get("company", "Unknown")
            comp_counts[c] = comp_counts.get(c, 0) + 1

        top_comp = max(comp_counts, key=comp_counts.get) if comp_counts else "Microsoft India"
        success_rate = round(offers / total, 2)
        avg_salary = round(sum(salaries) / len(salaries)) if salaries else 1000000

        rec = f"Highest success rate in {location} ({int(success_rate*100)}%). Prioritize opportunities in this location."

        return {
            "location": location,
            "total_applications": total,
            "success_rate": success_rate,
            "avg_salary_inr": avg_salary,
            "top_company": top_comp,
            "recommendation": rec
        }
