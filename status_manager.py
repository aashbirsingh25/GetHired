from typing import Dict, Any, List

VALID_STATUSES = ["applied", "pending", "interviewed", "offer", "accepted", "rejected", "referral", "archived"]

VALID_TRANSITIONS = {
    "applied": ["interviewed", "pending", "rejected", "offer", "referral", "archived"],
    "pending": ["interviewed", "rejected", "offer", "referral", "archived"],
    "interviewed": ["offer", "rejected", "accepted", "referral", "archived"],
    "offer": ["accepted", "rejected", "archived"],
    "referral": ["applied", "interviewed", "offer", "rejected", "pending", "archived"],
    "accepted": ["archived"],
    "rejected": ["archived"],
    "archived": []
}

def validate_transition(current_status: str, new_status: str) -> bool:
    curr = (current_status or "applied").lower()
    new_s = (new_status or "").lower()

    if new_s not in VALID_STATUSES:
        return False
    if curr == new_s:
        return True
    if new_s in ["referral", "archived"]:
        return True

    allowed = VALID_TRANSITIONS.get(curr, [])
    return new_s in allowed
