import os
import sys
import unittest

PROJECT_ROOT = r"c:\Users\Aashbir\OneDrive\Desktop\GetHired"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from local_scorer import score_locally
from hybrid_semantic_fallback import score_with_hybrid_semantic

class TestRankingFixesRegression(unittest.TestCase):
    
    def setUp(self):
        self.resume_skills = ["Python", "React", "Node.js", "AWS", "SQL", "Docker", "PostgreSQL", "C++"]

    # ----------------------------------------------------
    # A. UNKNOWN CAP TESTS
    # ----------------------------------------------------
    def test_quadeye_unknown_cap(self):
        res = score_locally(
            resume_skills=self.resume_skills,
            job_title="Intern - Software Developer (Product)",
            job_description="Quadeye Intern Software Developer opportunity for engineering candidates.",
            resume_exp_years=1
        )
        self.assertEqual(res["skill_confidence"], "unknown")
        self.assertLessEqual(res["score"], 65)

    def test_apple_software_engineer_unknown_cap(self):
        res = score_locally(
            resume_skills=self.resume_skills,
            job_title="Software engineer",
            job_description="Apple India Software Engineer posting in Hyderabad.",
            resume_exp_years=1
        )
        self.assertEqual(res["skill_confidence"], "unknown")
        self.assertLessEqual(res["score"], 65)

    def test_intel_intern_unknown_cap(self):
        res = score_locally(
            resume_skills=self.resume_skills,
            job_title="Software Engineer (Intern)",
            job_description="Intel India Software Engineer Intern posting.",
            resume_exp_years=1
        )
        self.assertEqual(res["skill_confidence"], "unknown")
        self.assertLessEqual(res["score"], 65)

    def test_unknown_non_tech_low_score(self):
        res = score_locally(
            resume_skills=self.resume_skills,
            job_title="Sales Manager",
            job_description="WebEngage Sales Manager position.",
            resume_exp_years=1
        )
        self.assertEqual(res["skill_confidence"], "unknown")
        self.assertLessEqual(res["score"], 45)

    def test_explicit_jobs_unaffected_by_unknown_cap(self):
        res = score_locally(
            resume_skills=self.resume_skills,
            job_title="Backend Engineer",
            job_description="Looking for Python, Node.js, SQL, Docker, PostgreSQL, React, AWS, C++ developers.",
            resume_exp_years=1
        )
        self.assertEqual(res["skill_confidence"], "explicit")
        self.assertGreater(res["score"], 65)

    # ----------------------------------------------------
    # B. EXPLICIT MATCH PRESERVATION TESTS
    # ----------------------------------------------------
    def test_explicit_high_match_not_capped(self):
        res = score_with_hybrid_semantic(
            resume_skills=self.resume_skills,
            job_title="Backend Engineer",
            job_description="High match: Python, React, Node.js, AWS, SQL, Docker, PostgreSQL, C++ required.",
            resume_exp_years=1
        )
        self.assertEqual(res["skill_confidence"], "explicit")
        self.assertGreaterEqual(res["score"], 70)
        self.assertNotEqual(res["score"], 65)

    # ----------------------------------------------------
    # C. SENIORITY CAP TESTS (cand_exp <= 2)
    # ----------------------------------------------------
    def test_senior_full_stack_capped_for_entry_level(self):
        res = score_locally(
            resume_skills=self.resume_skills,
            job_title="Senior Full Stack Engineer",
            job_description="Requires Python, React, Node.js, AWS, SQL, Docker, PostgreSQL, C++.",
            resume_exp_years=1
        )
        self.assertTrue(res["is_senior_job"])
        self.assertLessEqual(res["score"], 60)

    def test_senior_software_engineer_capped(self):
        res = score_locally(
            resume_skills=self.resume_skills,
            job_title="Senior Software Engineer",
            job_description="Senior Software Engineer role.",
            resume_exp_years=2
        )
        self.assertLessEqual(res["score"], 60)

    def test_lead_backend_engineer_capped(self):
        res = score_locally(
            resume_skills=self.resume_skills,
            job_title="Lead Backend Engineer",
            job_description="Lead Backend Engineer position requiring Python, SQL.",
            resume_exp_years=1
        )
        self.assertLessEqual(res["score"], 60)

    def test_principal_engineer_capped(self):
        res = score_locally(
            resume_skills=self.resume_skills,
            job_title="Principal Engineer",
            job_description="Principal Engineer leading architecture.",
            resume_exp_years=0
        )
        self.assertLessEqual(res["score"], 60)

    def test_engineering_manager_capped(self):
        res = score_locally(
            resume_skills=self.resume_skills,
            job_title="Engineering Manager",
            job_description="Managing engineering teams.",
            resume_exp_years=2
        )
        self.assertLessEqual(res["score"], 60)

    def test_senior_roles_not_capped_for_experienced_candidate(self):
        res = score_locally(
            resume_skills=self.resume_skills,
            job_title="Senior Software Engineer",
            job_description="Senior Software Engineer position.",
            resume_exp_years=5
        )
        self.assertGreater(res["score"], 60)

    def test_non_senior_roles_not_capped(self):
        res = score_locally(
            resume_skills=self.resume_skills,
            job_title="Software Engineer",
            job_description="Software Engineer position requiring Python, React, Node.js, AWS, SQL, Docker.",
            resume_exp_years=1
        )
        self.assertFalse(res["is_senior_job"])
        self.assertGreater(res["score"], 60)

    # ----------------------------------------------------
    # D. COMBINED EDGE CASES
    # ----------------------------------------------------
    def test_unknown_and_senior(self):
        res = score_locally(
            resume_skills=self.resume_skills,
            job_title="Senior Software Architect",
            job_description="Senior Software Architect position.",
            resume_exp_years=1
        )
        self.assertEqual(res["skill_confidence"], "unknown")
        self.assertTrue(res["is_senior_job"])
        self.assertLessEqual(res["score"], 60)

    def test_explicit_and_senior(self):
        res = score_locally(
            resume_skills=self.resume_skills,
            job_title="Senior Developer",
            job_description="Requires Python, React, AWS, Docker, PostgreSQL.",
            resume_exp_years=1
        )
        self.assertEqual(res["skill_confidence"], "explicit")
        self.assertTrue(res["is_senior_job"])
        self.assertLessEqual(res["score"], 60)

    def test_unknown_and_junior(self):
        res = score_locally(
            resume_skills=self.resume_skills,
            job_title="Software Developer",
            job_description="Generic software developer posting.",
            resume_exp_years=1
        )
        self.assertEqual(res["skill_confidence"], "unknown")
        self.assertFalse(res["is_senior_job"])
        self.assertLessEqual(res["score"], 65)

    def test_explicit_and_junior(self):
        res = score_locally(
            resume_skills=self.resume_skills,
            job_title="Software Developer",
            job_description="Requires Python, React, Node.js, AWS, SQL, Docker.",
            resume_exp_years=1
        )
        self.assertEqual(res["skill_confidence"], "explicit")
        self.assertFalse(res["is_senior_job"])
        self.assertGreater(res["score"], 65)

    # ----------------------------------------------------
    # E. PRODUCTION TRAP REGRESSION TESTS
    # ----------------------------------------------------
    def test_reporting_lines_do_not_trigger_executive_seniority_cap(self):
        """Job title 'Software Engineer' mentioning Director/VP/Staff in body should NOT be capped at 35."""
        res = score_locally(
            resume_skills=["Python", "FastAPI"],
            job_title="Software Engineer",
            job_description="We are looking for a Software Engineer with Python and FastAPI skills. You will report to the Director of Engineering and collaborate with Staff Engineers.",
            resume_exp_years=2
        )
        self.assertFalse(res["is_exec_senior_job"])
        self.assertGreaterEqual(res["score"], 75)

    def test_location_priority_not_fooled_by_description_mentions(self):
        """Job in Seattle listing Bangalore in description body should NOT be assigned Tier 0 location priority."""
        from priority_sorter import PrioritySorter
        sorter = PrioritySorter()
        job_seattle = {
            "title": "Software Engineer",
            "location": "Seattle, WA",
            "company": "GlobalCorp",
            "description": "We have engineering hubs in London, Seattle, and Bangalore."
        }
        loc_idx = sorter._get_location_priority_idx(job_seattle)
        self.assertGreater(loc_idx, 0)

if __name__ == "__main__":
    unittest.main()
