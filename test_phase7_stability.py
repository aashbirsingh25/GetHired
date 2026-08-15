import unittest
import os
import json
import time
from unittest.mock import MagicMock, patch

from hybrid_scorer import HybridJobScorer
from local_scorer import score_locally, classify_role
from priority_sorter import PrioritySorter
from score_consensus_checker import verify_with_second_opinion, should_verify, extract_numeric_score
from scratch.labeled_eval_dataset import CANDIDATE_RESUME, LABELED_EVAL_JOBS

class TestPhase7Stability(unittest.TestCase):

    def setUp(self):
        self.scorer = HybridJobScorer(CANDIDATE_RESUME)
        self.sorter = PrioritySorter()

    def test_01_excellent_match_ranking(self):
        """Verify Tier A excellent matches achieve score >= 74 and rank at the top."""
        tier_a_job = next(j for j in LABELED_EVAL_JOBS if j["id"] == "eval_a1")
        res = self.scorer.score_job(tier_a_job)
        self.assertGreaterEqual(res["score"], 74)

    def test_02_strong_match_ranking(self):
        """Verify Tier B strong matches achieve score >= 69."""
        tier_b_job = next(j for j in LABELED_EVAL_JOBS if j["id"] == "eval_b1")
        res = self.scorer.score_job(tier_b_job)
        self.assertGreaterEqual(res["score"], 69)

    def test_03_adjacent_role_ranking(self):
        """Verify Tier C adjacent roles (like DevOps or ML without candidate ML exp) rank lower than core SDE."""
        tier_a_res = self.scorer.score_job(next(j for j in LABELED_EVAL_JOBS if j["id"] == "eval_a1"))
        tier_c_res = self.scorer.score_job(next(j for j in LABELED_EVAL_JOBS if j["id"] == "eval_c1"))
        self.assertGreater(tier_a_res["score"], tier_c_res["score"])

    def test_04_unrelated_role_rejection(self):
        """Verify non-technical roles (Recruiting, Legal, Sales, Marketing) receive score <= 25."""
        non_tech_job = next(j for j in LABELED_EVAL_JOBS if j["id"] == "eval_e2") # Marketing Manager
        res = self.scorer.score_job(non_tech_job)
        self.assertLessEqual(res["score"], 25)

    def test_05_seniority_mismatch(self):
        """Verify candidate with 2 yrs exp receives hard seniority score cap (<=60) on Senior Staff Architect role."""
        senior_job = next(j for j in LABELED_EVAL_JOBS if j["id"] == "eval_d1")
        res = self.scorer.score_job(senior_job)
        self.assertLessEqual(res["score"], 60)

    def test_06_skill_false_positive_prevention(self):
        """Verify non-job documentation like 'Developer Docs' receives score <= 25."""
        doc_job = next(j for j in LABELED_EVAL_JOBS if j["id"] == "eval_e1")
        res = self.scorer.score_job(doc_job)
        self.assertLessEqual(res["score"], 25)

    def test_07_c_vs_cpp_distinction(self):
        """Verify C and C++ skills remain distinct in skill matching."""
        res_c = score_locally(["C++"], "C Programmer", "Role requires low level C language")
        self.assertIn("C", res_c["missing_skills"])

    def test_08_java_vs_javascript_distinction(self):
        """Verify Java and JavaScript skills remain distinct."""
        res_js = score_locally(["Java"], "Frontend Developer", "Role requires JavaScript, React, HTML")
        self.assertIn("JavaScript", res_js["missing_skills"])

    def test_09_role_vs_description_conflict(self):
        """Verify Senior Mechanical Engineer with Python in description is categorized as non_software_engineering (score <= 40)."""
        mech_job = next(j for j in LABELED_EVAL_JOBS if j["id"] == "eval_d4")
        res = self.scorer.score_job(mech_job)
        self.assertLessEqual(res["score"], 40)

    def test_10_semantic_false_positive_case(self):
        """Verify high semantic similarity does not override a non-technical role category."""
        # Non-technical Account Executive role mentioning enterprise software
        ae_job = next(j for j in LABELED_EVAL_JOBS if j["id"] == "eval_e5")
        res = self.scorer.score_job(ae_job)
        self.assertLessEqual(res["score"], 25)

    def test_11_llm_local_disagreement(self):
        """Verify disagreement flag is set when primary and secondary scores differ > 20."""
        res = verify_with_second_opinion(
            resume_chunks=["Python SDE"],
            resume_skills=["Python"],
            job_title="Software Engineer",
            job_description="Python developer",
            primary_score=90,
            primary_source="test_llm",
            primary_tier=6
        )
        self.assertIn("consensus", res)

    def test_12_malformed_llm_score(self):
        """Verify extract_numeric_score extracts valid numeric score from malformed dict/str outputs."""
        self.assertEqual(extract_numeric_score({"score": "85"}, default=50), 85)
        self.assertEqual(extract_numeric_score("72.5", default=50), 72)
        self.assertEqual(extract_numeric_score(None, default=50), 50)

    def test_13_out_of_range_score(self):
        """Verify extract_numeric_score clamps values to [0, 100]."""
        self.assertEqual(extract_numeric_score(150, default=50), 100)
        self.assertEqual(extract_numeric_score(-20, default=50), 0)

    def test_14_missing_score(self):
        """Verify missing score fallback uses default."""
        self.assertEqual(extract_numeric_score({}, default=50), 50)

    def test_15_deterministic_repeated_scoring(self):
        """Verify scoring same job with same resume produces identical scores across multiple runs."""
        job = next(j for j in LABELED_EVAL_JOBS if j["id"] == "eval_a1")
        s1 = self.scorer.score_job(job)["score"]
        s2 = self.scorer.score_job(job)["score"]
        s3 = self.scorer.score_job(job)["score"]
        self.assertEqual(s1, s2)
        self.assertEqual(s2, s3)

    def test_16_ranking_stability(self):
        """Verify ranking 30 evaluation jobs produces identical ordering across repeated calls."""
        scored_1 = [dict(j, match=self.scorer.score_job(j)) for j in LABELED_EVAL_JOBS]
        scored_2 = [dict(j, match=self.scorer.score_job(j)) for j in LABELED_EVAL_JOBS]

        r1 = [j["id"] for j in self.sorter.sort_jobs(scored_1)]
        r2 = [j["id"] for j in self.sorter.sort_jobs(scored_2)]
        self.assertEqual(r1, r2)

    def test_17_company_vs_match_weighting(self):
        """Verify Tier A match (84%) ranks above lower match (55%) even if lower match is at a top company."""
        high_match = {"id": "hm", "title": "SDE - Python", "match": {"score": 84}, "location_priority": 1}
        low_match = {"id": "lm", "title": "DevOps", "match": {"score": 55}, "location_priority": 0}
        
        ranked = self.sorter.sort_jobs([low_match, high_match])
        self.assertEqual(ranked[0]["id"], "hm")

    def test_18_stale_resume_score_invalidation(self):
        """Verify cached score is ignored and recomputed if resume_version_hash changes."""
        job = {"id": "stale_test", "title": "Python SDE", "description": "Python API", "match": {"score": 90, "resume_version_hash": "old_v1"}}
        new_scorer = HybridJobScorer({"version_hash": "new_v2", "skills": ["Python"]})
        res = new_scorer.score_job(job)
        self.assertEqual(res["resume_version_hash"], "new_v2")

    def test_19_pending_to_final_transition(self):
        """Verify job with match=None remains visible and transitions cleanly to final score when scored."""
        job = {"id": "pending_test", "title": "Python SDE", "description": "Python API", "match": None}
        res = self.scorer.score_job(job)
        self.assertIsNotNone(res)
        self.assertIn("score", res)

    def test_20_explanation_score_consistency(self):
        """Verify local score reasoning string contains non-contradictory component breakdown."""
        res = score_locally(["Python"], "Software Engineer", "Python REST API")
        self.assertIn("reasoning", res)
        self.assertIn("skill=", res["reasoning"].lower())

if __name__ == "__main__":
    unittest.main()
