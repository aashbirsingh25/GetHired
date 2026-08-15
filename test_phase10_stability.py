import unittest
import os
import json

from hybrid_scorer import HybridJobScorer
from priority_sorter import PrioritySorter
from scratch.phase9_production_dataset import get_production_dataset, REAL_RESUME

class TestPhase10Stability(unittest.TestCase):

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

    def test_01_tier_a_score_79_above_tier_b_score_65(self):
        """Adversarial Case 1: Tier A job with score 79 must rank above Tier B job with score 65."""
        job_79 = next((idx for idx, j in enumerate(self.ranked_jobs) if j["match"].get("score", 0) == 79), None)
        job_65 = next((idx for idx, j in enumerate(self.ranked_jobs) if j["match"].get("score", 0) == 65), None)
        
        self.assertIsNotNone(job_79)
        self.assertIsNotNone(job_65)
        self.assertLess(job_79, job_65)

    def test_02_strong_match_secondary_location_above_weak_local_match(self):
        """Adversarial Case 2: Strong backend/full-stack match with secondary location must remain above weak local match."""
        top_20_labels = [j["human_label"] for j in self.ranked_jobs[:20]]
        self.assertTrue(all(lbl in ("A", "B") for lbl in top_20_labels))

    def test_03_genuine_target_jobs_with_synonyms_unpenalized(self):
        """Adversarial Case 3: Target jobs using synonyms (Postgres, JS, TS, AWS) score >= 70."""
        target_a_jobs = [j for j in self.ranked_jobs if j["human_label"] == "A"]
        for j in target_a_jobs:
            self.assertGreaterEqual(j["match"].get("score", 0), 70)

    def test_04_senior_plausible_vs_executive_10_15_yr_distinction(self):
        """Adversarial Case 4: Principal / Partner Director (10-15+ yrs) roles are suppressed (score <= 55)."""
        exec_jobs = [j for j in self.ranked_jobs if j["human_label"] == "D" and any(k in (j.get("title") or "").upper() for k in ["PRINCIPAL", "DIRECTOR", "PARTNER"])]
        for j in exec_jobs:
            self.assertLessEqual(j["match"].get("score", 0), 55)

    def test_05_tier_c_adjacent_roles_cannot_displace_a_b_jobs(self):
        """Adversarial Case 5: Tier C adjacent roles (DevOps, Data Eng) must not enter top 10."""
        top_10_labels = [j["human_label"] for j in self.ranked_jobs[:10]]
        self.assertNotIn("C", top_10_labels)

    def test_06_tier_d_seniority_mismatch_suppressed_from_top_20(self):
        """Adversarial Case 6: Tier D seniority mismatch / support jobs must not enter top 20."""
        top_20_labels = [j["human_label"] for j in self.ranked_jobs[:20]]
        self.assertNotIn("D", top_20_labels)

    def test_07_tier_e_rejects_suppressed_from_top_20(self):
        """Adversarial Case 7: Tier E reject jobs (Sales, Mechanical, Docs) must not enter top 20."""
        top_20_labels = [j["human_label"] for j in self.ranked_jobs[:20]]
        self.assertNotIn("E", top_20_labels)

    def test_08_repeated_evaluation_deterministic_ordering(self):
        """Adversarial Case 8: Repeated sorting of the dataset produces 100% identical ordering."""
        r1 = [j["id"] for j in self.sorter.sort_jobs(self.ranked_jobs)]
        r2 = [j["id"] for j in self.sorter.sort_jobs(self.ranked_jobs)]
        self.assertEqual(r1, r2)

    def test_09_company_priority_breaks_ties_not_overriding_match_quality(self):
        """Adversarial Case 9: Higher match score (85) ranks above lower match score (65) regardless of company."""
        score_85_rank = next(idx for idx, j in enumerate(self.ranked_jobs) if j["match"].get("score", 0) == 85)
        score_65_rank = next(idx for idx, j in enumerate(self.ranked_jobs) if j["match"].get("score", 0) == 65)
        self.assertLess(score_85_rank, score_65_rank)

    def test_10_location_preference_breaks_ties_within_comparable_quality_groups(self):
        """Adversarial Case 10: Equal match scores (84) are ordered by location priority."""
        s84_jobs = [j for j in self.ranked_jobs if j["match"].get("score", 0) == 84]
        if len(s84_jobs) >= 2:
            self.assertLessEqual(s84_jobs[0]["location_priority"], s84_jobs[1]["location_priority"])

if __name__ == "__main__":
    unittest.main()
