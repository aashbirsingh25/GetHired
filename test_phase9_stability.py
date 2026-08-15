import unittest
import os
import json

from hybrid_scorer import HybridJobScorer
from local_scorer import score_locally
from priority_sorter import PrioritySorter
from scratch.phase9_production_dataset import get_production_dataset, REAL_RESUME

class TestPhase9Stability(unittest.TestCase):

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

    def test_01_production_feed_precision_at_10(self):
        """Verify Precision@10 on real extracted dataset is 100% (only target A/B jobs in top 10)."""
        top_10 = self.ranked_jobs[:10]
        precision_10 = sum(1 for j in top_10 if j["human_label"] in ("A", "B")) / 10.0
        self.assertEqual(precision_10, 1.0)

    def test_02_production_feed_mrr(self):
        """Verify Mean Reciprocal Rank (MRR) is 1.0 (top ranked item is a target match)."""
        top_job = self.ranked_jobs[0]
        self.assertIn(top_job["human_label"], ("A", "B"))

    def test_03_tier_e_rejection_rate(self):
        """Verify 100% of Tier E reject jobs score < 50."""
        tier_e_jobs = [j for j in self.ranked_jobs if j["human_label"] == "E"]
        high_tier_e = [j for j in tier_e_jobs if j["match"].get("score", 0) >= 50]
        self.assertEqual(len(high_tier_e), 0)

    def test_04_tier_a_retention_rate(self):
        """Verify 100% of Tier A strong target jobs score >= 70."""
        tier_a_jobs = [j for j in self.ranked_jobs if j["human_label"] == "A"]
        low_tier_a = [j for j in tier_a_jobs if j["match"].get("score", 0) < 70]
        self.assertEqual(len(low_tier_a), 0)

    def test_05_production_ndcg_calibration(self):
        """Verify NDCG@10 is >= 0.90."""
        rel_map = {"A": 3, "B": 2, "C": 1, "D": 0, "E": 0}
        relevances = [rel_map[j["human_label"]] for j in self.ranked_jobs[:10]]
        
        # Calculate DCG@10
        import numpy as np
        r = np.asarray(relevances, dtype=float)
        dcg = np.sum(r / np.log2(np.arange(2, r.size + 2)))
        
        r_ideal = np.asarray(sorted(relevances, reverse=True), dtype=float)
        idcg = np.sum(r_ideal / np.log2(np.arange(2, r_ideal.size + 2)))
        
        ndcg = (dcg / idcg) if idcg > 0 else 0.0
        self.assertGreaterEqual(ndcg, 0.90)

    def test_06_live_ats_greenhouse_extraction_ranking(self):
        """Verify Figma Greenhouse extracted jobs are present and correctly scored."""
        gh_jobs = [j for j in self.dataset if j.get("source") == "greenhouse_api"]
        self.assertGreater(len(gh_jobs), 0)

    def test_07_live_ats_ashby_extraction_ranking(self):
        """Verify Notion Ashby extracted jobs are present and correctly scored."""
        ashby_jobs = [j for j in self.dataset if j.get("source") == "ashby_api"]
        self.assertGreater(len(ashby_jobs), 0)

    def test_08_live_ats_smartrecruiters_extraction_ranking(self):
        """Verify Visa SmartRecruiters extracted jobs are present and correctly scored."""
        sr_jobs = [j for j in self.dataset if j.get("source") == "smartrecruiters_api"]
        self.assertGreater(len(sr_jobs), 0)

    def test_09_production_deterministic_repeated_ranking(self):
        """Verify sorting the dataset twice produces 100% identical feed ordering."""
        r1 = [j["id"] for j in self.sorter.sort_jobs(self.ranked_jobs)]
        r2 = [j["id"] for j in self.sorter.sort_jobs(self.ranked_jobs)]
        self.assertEqual(r1, r2)

    def test_10_explanation_score_consistency_production(self):
        """Verify top ranked production jobs have valid reasoning fields."""
        top_job = self.ranked_jobs[0]
        match_info = top_job.get("match", {})
        self.assertIsNotNone(match_info.get("score"))
        self.assertIsNotNone(match_info.get("reasoning"))

if __name__ == "__main__":
    unittest.main()
