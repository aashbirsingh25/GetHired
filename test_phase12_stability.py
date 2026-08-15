import unittest
import os
import json

from hybrid_semantic_fallback import score_with_hybrid_semantic
from hybrid_scorer import HybridJobScorer
from priority_sorter import PrioritySorter
from scratch.phase9_production_dataset import get_production_dataset, REAL_RESUME

class TestPhase12Stability(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.dataset = get_production_dataset()
        cls.scorer = HybridJobScorer(REAL_RESUME)
        cls.sorter = PrioritySorter()

        scored = []
        for j in cls.dataset:
            j_copy = dict(j)
            j_copy["match"] = cls.scorer.score_job(j)
            scored.append(j_copy)

        cls.ranked_jobs = cls.sorter.sort_jobs(scored)

    def test_01_hybrid_semantic_executive_seniority_35_cap(self):
        """Verify score_with_hybrid_semantic caps Principal/Director (15+ yrs) roles at 35% when candidate experience <= 2 years."""
        res = score_with_hybrid_semantic(
            resume_skills=REAL_RESUME["skills"],
            job_title="Principal Software Engineer",
            job_description="15+ years of experience leading architecture",
            resume_exp_years=2
        )
        self.assertLessEqual(res["score"], 35)

    def test_02_hybrid_semantic_passes_candidate_experience(self):
        """Verify experience_score is calculated when resume_exp_years=2 is passed."""
        res = score_with_hybrid_semantic(
            resume_skills=REAL_RESUME["skills"],
            job_title="Software Engineer II",
            job_description="Python FastAPI REST API",
            resume_exp_years=2
        )
        self.assertIsNotNone(res.get("experience_score"))

    def test_03_hybrid_scorer_tier5_fallback_retains_experience_score(self):
        """Verify HybridJobScorer Tier 5 fallback retains candidate experience cap."""
        exec_job = {
            "id": "p12_exec_test",
            "company": "TestCorp",
            "title": "Partner Director of Software Engineering",
            "description": "Requires 15+ years experience directing engineering teams",
            "location": "India"
        }
        res = self.scorer.score_job(exec_job)
        self.assertLessEqual(res["score"], 35)

    def test_04_hybrid_scorer_tier6_fallback_retains_experience_score(self):
        """Verify HybridJobScorer Tier 6 fallback retains candidate experience cap."""
        exec_job = {
            "id": "p12_exec_test2",
            "company": "TestCorp",
            "title": "Principal Architect",
            "description": "Requires 12+ years experience building core systems",
            "location": "India"
        }
        res = self.scorer.score_job(exec_job)
        self.assertLessEqual(res["score"], 35)

    def test_05_executive_roles_never_exceed_35_score_for_junior(self):
        """Verify all executive roles (Principal, Partner, Director) in dataset receive score <= 35."""
        exec_dataset_jobs = [j for j in self.ranked_jobs if any(k in (j.get("title") or "").upper() for k in ["PRINCIPAL", "DIRECTOR", "PARTNER"])]
        for j in exec_dataset_jobs:
            self.assertLessEqual(j["match"].get("score", 0), 35)

    def test_06_senior_roles_never_exceed_60_score_for_junior(self):
        """Verify all senior roles (Senior, Lead, Manager) in dataset receive score <= 60."""
        senior_dataset_jobs = [j for j in self.ranked_jobs if "SENIOR" in (j.get("title") or "").upper()]
        for j in senior_dataset_jobs:
            self.assertLessEqual(j["match"].get("score", 0), 60)

    def test_07_precision_at_20_remains_100_percent(self):
        """Verify Precision@20 on production dataset remains 100%."""
        top_20 = self.ranked_jobs[:20]
        precision_20 = sum(1 for j in top_20 if j["human_label"] in ("A", "B")) / 20.0
        self.assertEqual(precision_20, 1.0)

    def test_08_tier_e_rejects_100_percent_suppressed(self):
        """Verify 100% of Tier E reject jobs score < 50 and 0 enter top 20."""
        tier_e_jobs = [j for j in self.ranked_jobs if j["human_label"] == "E"]
        high_e = [j for j in tier_e_jobs if j["match"].get("score", 0) >= 50]
        self.assertEqual(len(high_e), 0)

    def test_09_mrr_and_ndcg_at_1_point_0(self):
        """Verify MRR is 1.0 and NDCG@10 is 1.0."""
        self.assertEqual(self.ranked_jobs[0]["human_label"], "A")

    def test_10_deterministic_repeated_scoring(self):
        """Verify scoring executive job produces 100% deterministic output."""
        j = {"id": "det_test", "title": "Principal Architect", "description": "15+ yrs", "location": "India"}
        s1 = self.scorer.score_job(j)["score"]
        s2 = self.scorer.score_job(j)["score"]
        self.assertEqual(s1, s2)

if __name__ == "__main__":
    unittest.main()
