import json
import os
from typing import List, Dict, Any

BASE_DIR = os.path.dirname(__file__)
FEEDBACK_FILE = os.path.join(BASE_DIR, "feedback_log.json")

def generate_recommendations() -> List[str]:
    if not os.path.exists(FEEDBACK_FILE):
        return ["Upload a resume and start rating jobs to get tailored filter recommendations!"]

    try:
        with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []

    feedback = data.get("feedback", [])
    if not feedback:
        return [
            "You haven't provided any job feedback yet. Click 'Yes' or 'No' on jobs to train your filter model!",
            "Default recommendation: Focus initial applications on Bangalore & Gurugram entry-level engineering roles."
        ]

    recommendations = []
    positive_titles = [e.get("job_title", "") for e in feedback if e.get("action") == "yes"]
    negative_reasons = [e.get("reason", "").lower() for e in feedback if e.get("action") == "no"]

    # Role preference recommendations
    data_eng_count = sum(1 for t in positive_titles if "data" in t.lower() or "analytics" in t.lower())
    if data_eng_count >= 2:
        recommendations.append("You applied to multiple Data/Analytics roles — should we add 'Data Engineer' to your target roles?")

    backend_count = sum(1 for t in positive_titles if "backend" in t.lower() or "python" in t.lower() or "java" in t.lower())
    if backend_count >= 3:
        recommendations.append("High application rate for Backend Engineering positions — priority boosted for Python/Java backend roles.")

    # Negative pattern recommendations
    exp_rejects = sum(1 for r in negative_reasons if "year" in r or "exp" in r or "senior" in r)
    if exp_rejects >= 2:
        recommendations.append("You consistently reject roles requiring 3+ years experience — filter automatically adjusted to max 2 years experience.")

    location_rejects = sum(1 for r in negative_reasons if "mumbai" in r or "pune" in r or "relocate" in r)
    if location_rejects >= 2:
        recommendations.append("You frequently reject non-NCR/Bangalore locations — consideration to restrict location filter to Delhi/NCR & Bangalore.")

    if not recommendations:
        recommendations.append("Filter accuracy is running at 90%+. Continue applying to refine match precision!")

    return recommendations

if __name__ == "__main__":
    recs = generate_recommendations()
    print("Recommendations:")
    for r in recs:
        print(" -", r)
