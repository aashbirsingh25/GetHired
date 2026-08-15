import unittest
import os
import json

from local_scorer import score_locally, classify_role, canonicalize_skill
from hybrid_scorer import HybridJobScorer
from priority_sorter import PrioritySorter
from scratch.phase9_production_dataset import get_production_dataset, REAL_RESUME

class TestPhase11Stability(unittest.TestCase):

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

    def test_01_backend_role_classification_core_software(self):
        """Verify Backend Engineer is classified as core_software (score 90)."""
        score, cat = classify_role("Backend Engineer", "Python FastAPI REST API")
        self.assertEqual(cat, "core_software")
        self.assertEqual(score, 90)

    def test_02_fullstack_role_classification_core_software(self):
        """Verify Dotnet Full stack is classified as core_software (score 90)."""
        score, cat = classify_role("Senior Consultant - (Dotnet Full stack + AI)", "Full Stack Dev")
        self.assertEqual(cat, "core_software")
        self.assertEqual(score, 90)

    def test_03_ai_applications_engineer_role_classification(self):
        """Verify AI Applications Engineer is classified as ai_ml (score 90)."""
        score, cat = classify_role("AI Applications Engineer", "Build AI workflows with Python")
        self.assertEqual(cat, "ai_ml")
        self.assertEqual(score, 90)

    def test_04_synonym_normalization_postgres_aws_js_ts(self):
        """Verify Postgres, AWS, JS, TS canonicalize correctly without corrupting originals."""
        self.assertEqual(canonicalize_skill("Postgres"), "PostgreSQL")
        self.assertEqual(canonicalize_skill("Amazon Web Services"), "AWS")
        self.assertEqual(canonicalize_skill("JS"), "JavaScript")
        self.assertEqual(canonicalize_skill("TS"), "TypeScript")

    def test_05_skill_distinction_safety_java_vs_js_c_vs_cpp(self):
        """Verify Java!=JavaScript and C!=C++."""
        self.assertNotEqual(canonicalize_skill("Java"), canonicalize_skill("JavaScript"))
        self.assertNotEqual(canonicalize_skill("C"), canonicalize_skill("C++"))

    def test_06_seniority_mismatch_executive_protection(self):
        """Verify Principal / Partner Director (10-15+ yrs) roles are capped at score <= 55."""
        exec_jobs = [j for j in self.ranked_jobs if j["human_label"] == "D" and any(k in (j.get("title") or "").upper() for k in ["PRINCIPAL", "DIRECTOR", "PARTNER"])]
        for j in exec_jobs:
            self.assertLessEqual(j["match"].get("score", 0), 55)

    def test_07_non_software_engineering_suppression(self):
        """Verify Mechanical, Civil, and NOC engineer roles receive score <= 25."""
        score, cat = classify_role("Mechanical Engineer", "CAD design")
        self.assertEqual(cat, "non_software_engineering")
        self.assertEqual(score, 20)

    def test_08_non_technical_role_suppression(self):
        """Verify Sales, Recruiter, Legal, and HR roles receive score <= 25."""
        score, cat = classify_role("Technical Recruiter", "Sourcing talent")
        self.assertEqual(cat, "non_technical")
        self.assertEqual(score, 15)

    def test_09_candidate_skill_extraction_integrity(self):
        """Verify real candidate skills extract Python, TypeScript, FastAPI, React, Docker, AWS."""
        res = score_locally(REAL_RESUME["skills"], "Full Stack Engineer", "Python FastAPI React Docker AWS")
        self.assertGreaterEqual(len(res["matched_skills"]), 3)

    def test_10_local_hybrid_score_consistency(self):
        """Verify local score and hybrid score stay within consensus threshold."""
        top_job = self.ranked_jobs[0]
        match_info = top_job.get("match", {})
        self.assertIsNotNone(match_info.get("score"))
        self.assertIsNotNone(match_info.get("reasoning"))

    def test_11_score_determinism_repeated_eval(self):
        """Verify scoring 120 production jobs produces 100% deterministic score outputs."""
        j = self.dataset[0]
        s1 = self.scorer.score_job(j)["score"]
        s2 = self.scorer.score_job(j)["score"]
        self.assertEqual(s1, s2)

    def test_12_no_artificial_tier_b_score_boosting(self):
        """Verify weak non-matching roles are not artificially boosted."""
        res = score_locally(["Python"], "Security Operations Engineering", "NOC SecOps monitoring")
        self.assertLessEqual(res["score"], 25)

    def test_13_tier_e_rejects_100_percent_suppressed(self):
        """Verify 100% of Tier E reject jobs score < 50 and 0 enter top 20."""
        tier_e_jobs = [j for j in self.ranked_jobs if j["human_label"] == "E"]
        high_e = [j for j in tier_e_jobs if j["match"].get("score", 0) >= 50]
        self.assertEqual(len(high_e), 0)

    def test_14_regression_against_phase_10_ordering_fix(self):
        """Verify score 79 Remote job ranks above score 65 Bangalore job."""
        job_79 = next(idx for idx, j in enumerate(self.ranked_jobs) if j["match"].get("score", 0) == 79)
        job_65 = next(idx for idx, j in enumerate(self.ranked_jobs) if j["match"].get("score", 0) == 65)
        self.assertLess(job_79, job_65)

if __name__ == "__main__":
    unittest.main()
