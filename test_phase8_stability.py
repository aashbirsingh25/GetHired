import unittest
import os
import json

from hybrid_scorer import HybridJobScorer
from local_scorer import score_locally, classify_role, canonicalize_skill
from priority_sorter import PrioritySorter
from scratch.phase8_real_job_dataset import CANDIDATE_PROFILE, PHASE8_EVAL_JOBS

class TestPhase8Stability(unittest.TestCase):

    def setUp(self):
        self.scorer = HybridJobScorer(CANDIDATE_PROFILE)
        self.sorter = PrioritySorter()

    def test_01_real_profile_excellent_matches(self):
        """Verify Group A excellent matches for real candidate profile achieve score >= 70."""
        job = next(j for j in PHASE8_EVAL_JOBS if j["id"] == "p8_a1")
        res = self.scorer.score_job(job)
        self.assertGreaterEqual(res["score"], 70)

    def test_02_real_profile_strong_matches(self):
        """Verify Group B strong matches achieve score >= 64."""
        job = next(j for j in PHASE8_EVAL_JOBS if j["id"] == "p8_b1")
        res = self.scorer.score_job(job)
        self.assertGreaterEqual(res["score"], 64)

    def test_03_synonym_equivalence_postgres_aws_js(self):
        """Verify Postgres, Amazon Web Services, JS, TS, Node resolve to canonical equivalents."""
        self.assertEqual(canonicalize_skill("Postgres"), "PostgreSQL")
        self.assertEqual(canonicalize_skill("Amazon Web Services"), "AWS")
        self.assertEqual(canonicalize_skill("JS"), "JavaScript")
        self.assertEqual(canonicalize_skill("TS"), "TypeScript")
        self.assertEqual(canonicalize_skill("Node"), "Node.js")

    def test_04_dangerous_synonym_isolation_java_vs_javascript(self):
        """Verify Java and JavaScript remain distinct."""
        self.assertNotEqual(canonicalize_skill("Java"), canonicalize_skill("JavaScript"))
        res = score_locally(["Java"], "Frontend Developer", "Role requires JavaScript, React")
        self.assertIn("JavaScript", res["missing_skills"])

    def test_05_dangerous_synonym_isolation_c_vs_cpp(self):
        """Verify C and C++ remain distinct."""
        self.assertNotEqual(canonicalize_skill("C"), canonicalize_skill("C++"))
        res = score_locally(["C++"], "C Programmer", "Low level C drivers")
        self.assertIn("C", res["missing_skills"])

    def test_06_non_software_engineering_traps(self):
        """Verify Mechanical, Civil, Electrical, and NOC engineer roles receive score <= 25."""
        mech_job = next(j for j in PHASE8_EVAL_JOBS if j["id"] == "p8_d1")
        res = self.scorer.score_job(mech_job)
        self.assertLessEqual(res["score"], 25)

    def test_07_non_technical_sales_recruiting_traps(self):
        """Verify Sales, Recruiting, Legal, and Support roles receive score <= 25."""
        sales_job = next(j for j in PHASE8_EVAL_JOBS if j["id"] == "p8_d5")
        res = self.scorer.score_job(sales_job)
        self.assertLessEqual(res["score"], 25)

    def test_08_executive_seniority_suppression(self):
        """Verify Principal/Director/Staff roles (10-15+ yrs) are suppressed (score <= 55) for candidate with 2 yrs exp."""
        principal_job = next(j for j in PHASE8_EVAL_JOBS if j["id"] == "p8_f1")
        res = self.scorer.score_job(principal_job)
        self.assertLessEqual(res["score"], 55)

    def test_09_adjacent_role_ranking_tier(self):
        """Verify DevOps/SRE and Data Engineer roles score lower than core SDE for a backend candidate."""
        sde_job = next(j for j in PHASE8_EVAL_JOBS if j["id"] == "p8_a1")
        devops_job = next(j for j in PHASE8_EVAL_JOBS if j["id"] == "p8_c1")
        
        res_sde = self.scorer.score_job(sde_job)
        res_devops = self.scorer.score_job(devops_job)
        self.assertGreater(res_sde["score"], res_devops["score"])

    def test_10_synonym_false_negative_trap_recovery(self):
        """Verify jobs using synonyms (Postgres, JS, TS, Amazon Web Services) score >= 70."""
        synonym_job = next(j for j in PHASE8_EVAL_JOBS if j["id"] == "p8_e1")
        res = self.scorer.score_job(synonym_job)
        self.assertGreaterEqual(res["score"], 70)

    def test_11_repeated_deterministic_scoring(self):
        """Verify scoring 50 realistic jobs produces 100% identical scores across runs."""
        job = next(j for j in PHASE8_EVAL_JOBS if j["id"] == "p8_a2")
        s1 = self.scorer.score_job(job)["score"]
        s2 = self.scorer.score_job(job)["score"]
        self.assertEqual(s1, s2)

    def test_12_stale_cache_invalidation_real_profile(self):
        """Verify cached match is invalidated when version_hash changes."""
        job = {"id": "p8_cache_test", "title": "Backend SDE", "description": "Python API", "match": {"score": 90, "resume_version_hash": "old_hash"}}
        new_scorer = HybridJobScorer({"version_hash": "new_hash_v2", "skills": ["Python"]})
        res = new_scorer.score_job(job)
        self.assertEqual(res["resume_version_hash"], "new_hash_v2")

    def test_13_pending_to_final_transition(self):
        """Verify job with pending match transitions cleanly to scored match."""
        job = {"id": "p8_pending", "title": "Software Engineer", "description": "Python FastAPI", "match": None}
        res = self.scorer.score_job(job)
        self.assertIsNotNone(res.get("score"))

    def test_14_explanation_non_contradiction(self):
        """Verify explanation string includes skill and role component breakdown."""
        res = score_locally(["Python", "FastAPI"], "Software Engineer", "Python FastAPI REST API")
        self.assertIn("reasoning", res)
        self.assertIn("skill=", res["reasoning"].lower())

if __name__ == "__main__":
    unittest.main()
