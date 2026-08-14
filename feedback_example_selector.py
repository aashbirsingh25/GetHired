import json
import os
import re
from typing import Dict, Any, List, Tuple

BASE_DIR = os.path.dirname(__file__)
FEEDBACK_FILE = os.path.join(BASE_DIR, "feedback_log.json")
SCORING_LOG_FILE = os.path.join(BASE_DIR, "scoring_log.json")

_feedback_cache = {}

class FeedbackExampleSelector:
    def __init__(self, feedback_path: str = FEEDBACK_FILE, scoring_log_path: str = SCORING_LOG_FILE):
        self.feedback_path = feedback_path
        self.scoring_log_path = scoring_log_path

    def _load_feedback(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.feedback_path):
            return []
        try:
            mtime = os.path.getmtime(self.feedback_path)
            if self.feedback_path in _feedback_cache:
                cached_mtime, cached_data = _feedback_cache[self.feedback_path]
                if cached_mtime == mtime:
                    return cached_data
            with open(self.feedback_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                items = data.get("feedback", [])
                _feedback_cache[self.feedback_path] = (mtime, items)
                return items
        except Exception:
            return []

    def get_personalization_stats(self) -> Dict[str, Any]:
        feedback = self._load_feedback()
        total_count = len(feedback)
        active = total_count >= 5

        jobs_scored_count = 0
        if os.path.exists(self.scoring_log_path):
            try:
                with open(self.scoring_log_path, "r", encoding="utf-8") as f:
                    s_data = json.load(f)
                    logs = s_data.get("logs", [])
                    jobs_scored_count = sum(1 for log in logs if log.get("personalization_examples_used", 0) > 0 or log.get("personalization_active", False))
            except Exception:
                pass

        return {
            "total_feedback_count": total_count,
            "personalization_active": active,
            "jobs_scored_with_personalization": jobs_scored_count,
            "recent_examples_injected": min(total_count, 5)
        }

    def select_examples(self, job_dict: Dict[str, Any], max_positive: int = 2, max_negative: int = 2) -> List[Dict[str, Any]]:
        feedback = self._load_feedback()
        if len(feedback) < 5:
            return []

        title = (job_dict.get("title") or "").lower()
        desc = (job_dict.get("description") or "").lower()
        skills = [s.lower() for s in (job_dict.get("skills") or [])]

        target_tokens = set(re.findall(r'\w+', title + " " + " ".join(skills)))

        scored_items = []
        for idx, item in enumerate(feedback):
            item_title = (item.get("job_title") or "").lower()
            item_reason = (item.get("reason") or "").lower()
            item_tokens = set(re.findall(r'\w+', item_title + " " + item_reason))

            relevance = len(target_tokens.intersection(item_tokens))
            recency_bonus = idx * 0.1  # slightly favor more recent entries
            total_score = relevance + recency_bonus

            scored_items.append((total_score, item))

        positives = [item for _, item in sorted(scored_items, key=lambda x: x[0], reverse=True) if item.get("action") == "yes"]
        negatives = [item for _, item in sorted(scored_items, key=lambda x: x[0], reverse=True) if item.get("action") == "no"]

        selected = positives[:max_positive] + negatives[:max_negative]
        return selected

    def build_prompt_injection(self, job_dict: Dict[str, Any]) -> Tuple[str, int]:
        examples = self.select_examples(job_dict)
        if not examples:
            return "", 0

        lines = ["\nPERSONALIZATION & USER PREFERENCE HISTORY (User feedback on previous roles):"]
        for ex in examples:
            action_label = "approved" if ex.get("action") == "yes" else "rejected"
            title = ex.get("job_title") or "Role"
            company = ex.get("company") or "Company"
            reason = ex.get("reason") or "No specific reason"
            score = ex.get("job_match_score", 50)
            lines.append(f'- User {action_label} "{title}" at {company}: "{reason}" (Match score was {score}%)')

        lines.append("Calibrate your score and reasoning based on these explicit user preferences.\n")
        return "\n".join(lines), len(examples)
