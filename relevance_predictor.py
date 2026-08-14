import json
import os
import re
import time
from typing import Dict, Any, List
from company_analyzer import CompanyAnalyzer
from feedback_example_selector import FeedbackExampleSelector

BASE_DIR = os.path.dirname(__file__)
JOBS_STORE_FILE = os.path.join(BASE_DIR, "jobs_store.json")
FEEDBACK_FILE = os.path.join(BASE_DIR, "feedback_log.json")

_file_cache = {}

def load_json_cached(filepath, default):
    if not os.path.exists(filepath):
        return default
    try:
        mtime = os.path.getmtime(filepath)
        if filepath in _file_cache:
            cached_mtime, cached_data = _file_cache[filepath]
            if cached_mtime == mtime:
                return cached_data
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            _file_cache[filepath] = (mtime, data)
            return data
    except Exception:
        return default

class RelevancePredictor:
    _cache: Dict[str, Any] = {}

    def __init__(self, jobs_store_path: str = JOBS_STORE_FILE, feedback_path: str = FEEDBACK_FILE):
        self.jobs_store_path = jobs_store_path
        self.feedback_path = feedback_path
        self.company_analyzer = CompanyAnalyzer()
        self.example_selector = FeedbackExampleSelector(feedback_path=feedback_path)
        self.cache_ttl_seconds = 3600

    def predict_relevance(self, company_name: str, job_title: str) -> float:
        company_clean = (company_name or "").strip().lower()
        title_clean = (job_title or "").strip().lower()
        cache_key = f"{company_clean}:{title_clean}"

        now = time.time()
        if cache_key in RelevancePredictor._cache:
            cached_time, cached_val = RelevancePredictor._cache[cache_key]
            if now - cached_time < self.cache_ttl_seconds:
                return cached_val

        # 1. Company success rate (weight 40%)
        company_sub_score = 0.5
        try:
            analytics = self.company_analyzer.analyze_company(company_name)
            total_apps = analytics.get("total_applications", 0)
            if total_apps > 0:
                succ_rate = analytics.get("success_rate", 0.0)
                company_sub_score = max(0.0, min(1.0, float(succ_rate)))
        except Exception:
            company_sub_score = 0.5

        # 2. Historical avg match score for this company/title (weight 30%)
        history_sub_score = 0.5
        try:
            jobs_data = load_json_cached(self.jobs_store_path, {"jobs": []})
            jobs = jobs_data.get("jobs", [])
            comp_jobs = [
                j for j in jobs
                if (j.get("company") or "").strip().lower() == company_clean
            ]
            if comp_jobs:
                scores = [
                    float(j.get("match", {}).get("score", 0))
                    for j in comp_jobs
                    if j.get("match") and j.get("match", {}).get("score") is not None
                ]
                if scores:
                    avg_score = sum(scores) / len(scores)
                    history_sub_score = max(0.0, min(1.0, avg_score / 100.0))
        except Exception:
            history_sub_score = 0.5

        # 3. Similarity to past "yes" feedback (weight 30%)
        feedback_sub_score = 0.5
        try:
            feedback_data = load_json_cached(self.feedback_path, {"feedback": []})
            feedback_items = feedback_data.get("feedback", [])

            if feedback_items:
                title_tokens = set(re.findall(r'\w+', title_clean + " " + company_clean))

                pos_matches = 0
                total_matches = 0

                for item in feedback_items:
                    item_title = (item.get("job_title") or "").lower()
                    item_comp = (item.get("company") or "").lower()
                    item_tokens = set(re.findall(r'\w+', item_title + " " + item_comp))
                    overlap = len(title_tokens.intersection(item_tokens))

                    if overlap > 0:
                        total_matches += 1
                        if item.get("action") == "yes":
                            pos_matches += 1

                if total_matches > 0:
                    feedback_sub_score = max(0.0, min(1.0, pos_matches / float(total_matches)))
                else:
                    # Check ratio of overall yes feedback if no token overlap
                    yes_count = sum(1 for item in feedback_items if item.get("action") == "yes")
                    feedback_sub_score = max(0.0, min(1.0, yes_count / float(len(feedback_items))))
        except Exception:
            feedback_sub_score = 0.5

        # Combine weighted sub-scores: 40% company, 30% history match, 30% feedback
        final_relevance = round(
            0.40 * company_sub_score + 0.30 * history_sub_score + 0.30 * feedback_sub_score,
            2
        )
        final_relevance = max(0.0, min(1.0, final_relevance))

        RelevancePredictor._cache[cache_key] = (now, final_relevance)
        return final_relevance
