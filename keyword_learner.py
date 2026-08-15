import json
import os
import re
from datetime import datetime
from threshold_optimizer import log_auto_improvement

BASE_DIR = os.path.dirname(__file__)
FILTERS_FILE = os.path.join(BASE_DIR, "filters.json")

def learn_from_positive_feedback(job_title: str, reason: str):
    if not os.path.exists(FILTERS_FILE):
        return None

    with open(FILTERS_FILE, "r", encoding="utf-8") as f:
        filters = json.load(f)

    target_roles = filters.get("target_role", [])
    
    # Extract clean title phrase
    clean_title = re.sub(r"[\(\)\[\]\{\}\d\+]+", "", job_title).strip()
    words = clean_title.split(",")
    candidate_kw = words[0].strip()

    if candidate_kw and candidate_kw not in target_roles and len(candidate_kw) <= 40:
        target_roles.append(candidate_kw)
        filters["target_role"] = target_roles

        from scan_coordinator import save_json
        save_json(FILTERS_FILE, filters)

        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "keyword_learned",
            "filter": "target_role",
            "new_keyword": candidate_kw,
            "source": f"positive_feedback_job_{job_title[:20]}"
        }
        log_auto_improvement(entry)
        return entry
    return None

def learn_from_negative_feedback(job_title: str, reason: str):
    if not os.path.exists(FILTERS_FILE):
        return None

    with open(FILTERS_FILE, "r", encoding="utf-8") as f:
        filters = json.load(f)

    exclude_kws = filters.get("exclude_keywords", [])
    reason_upper = (reason or "").upper()
    
    new_excludes = []
    if "SENIOR" in reason_upper and "Senior" not in exclude_kws:
        new_excludes.append("Senior")
    if "LEAD" in reason_upper and "Lead" not in exclude_kws:
        new_excludes.append("Lead")
    if "MANAGER" in reason_upper and "Manager" not in exclude_kws:
        new_excludes.append("Manager")
    if "PRINCIPAL" in reason_upper and "Principal" not in exclude_kws:
        new_excludes.append("Principal")

    if new_excludes:
        for x in new_excludes:
            if x not in exclude_kws:
                exclude_kws.append(x)
        filters["exclude_keywords"] = exclude_kws

        from scan_coordinator import save_json
        save_json(FILTERS_FILE, filters)

        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "exclude_keyword_learned",
            "filter": "exclude_keywords",
            "new_keywords": new_excludes,
            "source": f"negative_feedback_reason_{reason[:20]}"
        }
        log_auto_improvement(entry)
        return entry
    return None
