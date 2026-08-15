import os
import json
import math
import re
from typing import Dict, Any, List, Tuple
from embedding_service import EmbeddingService
from priority_sorter import matches_global_filters

BASE_DIR = os.path.dirname(__file__)
FILTERS_FILE = os.path.join(BASE_DIR, "filters.json")
METRICS_FILE = os.path.join(BASE_DIR, "filter_metrics.json")

def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)

class SemanticFilterEngine:
    def __init__(self, filters_path=FILTERS_FILE, metrics_path=METRICS_FILE):
        self.filters_path = filters_path
        self.metrics_path = metrics_path
        self.embedder = EmbeddingService()
        self.filters = self._load_filters()
        self.metrics = self._load_metrics()
        self.thresholds = {
            "role": self.metrics.get("role_filter", {}).get("threshold", 0.80),
            "location": self.metrics.get("location_filter", {}).get("threshold", 0.80),
            "experience": self.metrics.get("experience_filter", {}).get("threshold", 0.80),
            "exclude": self.metrics.get("exclude_filter", {}).get("threshold", 0.85)
        }
        self._precompute_embeddings()

    def _load_filters(self) -> Dict[str, Any]:
        if os.path.exists(self.filters_path):
            with open(self.filters_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "target_role": ["Software Engineer", "Intern", "Fresher", "Entry-level"],
            "target_location": ["Gurugram", "Bangalore", "Delhi", "Remote"],
            "target_experience": ["0 years", "Fresher", "Entry-level"],
            "exclude_keywords": ["Senior", "Lead", "Manager", "Principal"]
        }

    def _load_metrics(self) -> Dict[str, Any]:
        if os.path.exists(self.metrics_path):
            with open(self.metrics_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "role_filter": {"threshold": 0.80, "precision": 0.92, "recall": 0.85, "total_evaluated": 0, "positive_feedback": 0, "negative_feedback": 0},
            "location_filter": {"threshold": 0.80, "precision": 0.90, "recall": 0.88, "total_evaluated": 0, "positive_feedback": 0, "negative_feedback": 0},
            "experience_filter": {"threshold": 0.80, "precision": 0.88, "recall": 0.82, "total_evaluated": 0, "positive_feedback": 0, "negative_feedback": 0},
            "exclude_filter": {"threshold": 0.85, "precision": 0.95, "recall": 0.90, "total_evaluated": 0, "positive_feedback": 0, "negative_feedback": 0}
        }

    def _precompute_embeddings(self):
        self.role_embs = [self.embedder.get_embedding(r) for r in self.filters.get("target_role", [])]
        self.loc_embs = [self.embedder.get_embedding(l) for l in self.filters.get("target_location", [])]
        self.exp_embs = [self.embedder.get_embedding(e) for e in self.filters.get("target_experience", [])]
        self.exclude_embs = [self.embedder.get_embedding(x) for x in self.filters.get("exclude_keywords", [])]

    def update_threshold(self, filter_type: str, new_threshold: float):
        if filter_type in self.thresholds:
            self.thresholds[filter_type] = round(new_threshold, 2)
            metric_key = f"{filter_type}_filter"
            if metric_key in self.metrics:
                self.metrics[metric_key]["threshold"] = round(new_threshold, 2)
            from scan_coordinator import save_json
            save_json(self.metrics_path, self.metrics)

    def filter_job(self, job: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        title = job.get("title", "")
        location = job.get("location", "")
        description = job.get("description", "")
        combined = title + " " + location + " " + description

        # Embed job title
        title_emb = self.embedder.get_embedding(title)
        loc_emb = self.embedder.get_embedding(location)

        # 0. Global Filters Check (Owned exclusively by Auto-Scout background search)
        global_passed = True

        # 1. Role Match
        max_role_sim = max([cosine_similarity(title_emb, r_emb) for r_emb in self.role_embs], default=0.0)
        # Substring keyword check fallback
        role_kw_match = any(re.search(r"\b" + re.escape(r) + r"\b", title, re.I) for r in self.filters.get("target_role", [])) if isinstance(self.filters.get("target_role"), list) else True
        role_passed = (max_role_sim >= self.thresholds["role"]) or role_kw_match or (len(self.role_embs) == 0)

        # 2. Location Match
        max_loc_sim = max([cosine_similarity(loc_emb, l_emb) for l_emb in self.loc_embs], default=0.0)
        loc_kw_match = any(l.lower() in location.lower() or l.lower() in combined.lower() for l in self.filters.get("target_location", [])) if isinstance(self.filters.get("target_location"), list) and isinstance(self.filters.get("target_location")[0], str) else True
        location_passed = (max_loc_sim >= self.thresholds["location"]) or loc_kw_match or ("india" in location.lower()) or (len(self.loc_embs) == 0)

        # 3. Exclude Check
        max_exclude_sim = max([cosine_similarity(title_emb, x_emb) for x_emb in self.exclude_embs], default=0.0)
        exclude_kw_match = any(re.search(r"\b" + re.escape(x) + r"\b", title, re.I) for x in self.filters.get("exclude_keywords", [])) if isinstance(self.filters.get("exclude_keywords"), list) else False
        exclude_passed = not (exclude_kw_match or max_exclude_sim > self.thresholds["exclude"])

        # Overall Filter Pass (AND logic)
        passed_all = global_passed and role_passed and location_passed and exclude_passed

        details = {
            "passed": passed_all,
            "global_passed": global_passed,
            "role_passed": role_passed,
            "location_passed": location_passed,
            "exclude_passed": exclude_passed,
            "role_similarity": round(max_role_sim, 2),
            "location_similarity": round(max_loc_sim, 2),
            "exclude_similarity": round(max_exclude_sim, 2)
        }

        return passed_all, details
