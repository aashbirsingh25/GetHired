import os
import sys
import unittest

PROJECT_ROOT = r"c:\Users\Aashbir\OneDrive\Desktop\GetHired"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from local_scorer import score_locally

class TestSeniorityTitleRegression(unittest.TestCase):

    def setUp(self):
        self.resume_skills = ["Python", "React", "Node.js", "AWS", "SQL"]

    def test_senior_underscore(self):
        res = score_locally(self.resume_skills, "Senior_Engineer", "Description")
        self.assertTrue(res["is_senior_job"])
        self.assertLessEqual(res["score"], 60)

    def test_senior_hyphen(self):
        res = score_locally(self.resume_skills, "Senior-Engineer", "Description")
        self.assertTrue(res["is_senior_job"])
        self.assertLessEqual(res["score"], 60)

    def test_senior_slash(self):
        res = score_locally(self.resume_skills, "Senior/Engineer", "Description")
        self.assertTrue(res["is_senior_job"])
        self.assertLessEqual(res["score"], 60)

    def test_architect_underscore(self):
        res = score_locally(self.resume_skills, "Architect_6", "Description")
        self.assertTrue(res["is_senior_job"])
        self.assertLessEqual(res["score"], 60)

    def test_lead_backend_underscore(self):
        res = score_locally(self.resume_skills, "Lead_Backend_Engineer", "Description")
        self.assertTrue(res["is_senior_job"])
        self.assertLessEqual(res["score"], 60)

    def test_principal_hyphen(self):
        res = score_locally(self.resume_skills, "Principal-Engineer", "Description")
        self.assertTrue(res["is_senior_job"])
        self.assertLessEqual(res["score"], 60)

    def test_seniority_engineer_should_not_match(self):
        res = score_locally(self.resume_skills, "Seniority Engineer", "Description", resume_exp_years=1)
        self.assertFalse(res["is_senior_job"])

    def test_leadership_engineer_should_not_match(self):
        res = score_locally(self.resume_skills, "Leadership Engineer", "Description", resume_exp_years=1)
        self.assertFalse(res["is_senior_job"])

    def test_managerial_engineer_should_not_match(self):
        res = score_locally(self.resume_skills, "Managerial Engineer", "Description", resume_exp_years=1)
        self.assertFalse(res["is_senior_job"])

    def test_architecture_engineer_should_not_match(self):
        res = score_locally(self.resume_skills, "Architecture Engineer", "Description", resume_exp_years=1)
        self.assertFalse(res["is_senior_job"])

    def test_fractal_failing_job_specific(self):
        res = score_locally(
            resume_skills=self.resume_skills,
            job_title="Data Engineer_Business Intelligence_Senior Consultant_Architect_6",
            job_description="Workday job posting: Senior Data Analytics Consultant (London)",
            resume_exp_years=1
        )
        self.assertTrue(res["is_senior_job"])
        self.assertLessEqual(res["score"], 60)

if __name__ == "__main__":
    unittest.main()
