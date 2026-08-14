import os
import sys
import unittest

PROJECT_ROOT = r"c:\Users\Aashbir\OneDrive\Desktop\GetHired"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from local_scorer import score_locally, KNOWN_TECH_SKILLS
from hybrid_semantic_fallback import score_with_hybrid_semantic

class TestScorerBugs(unittest.TestCase):

    def setUp(self):
        self.candidate_skills = [
            "Python", "FastAPI", "React", "TypeScript", "JavaScript", 
            "Docker", "PostgreSQL", "MongoDB", "Redis", "AWS", "Git", "CI/CD"
        ]

    def test_01_software_engineer_does_not_flag_engineer_as_missing_skill(self):
        """Regression Test 1: 'Software Engineer' title must not produce 'Engineer' or 'Developer' as missing tech skills."""
        # Ensure Engineer and Developer are NOT in KNOWN_TECH_SKILLS
        self.assertNotIn("Engineer", KNOWN_TECH_SKILLS)
        self.assertNotIn("Developer", KNOWN_TECH_SKILLS)
        self.assertNotIn("Agile", KNOWN_TECH_SKILLS)
        self.assertNotIn("Scrum", KNOWN_TECH_SKILLS)

        res = score_locally(
            resume_skills=self.candidate_skills,
            job_title="Software Engineer",
            job_description="Seeking a Software Engineer to work on backend systems."
        )

        missing = res.get("missing_skills", [])
        self.assertNotIn("Engineer", missing, "Role noun 'Engineer' was incorrectly flagged as a missing technical skill!")
        self.assertNotIn("Developer", missing, "Role noun 'Developer' was incorrectly flagged as a missing technical skill!")

    def test_02_matched_stack_scores_higher_than_unmatched_stack(self):
        """Regression Test 2: Job requiring Python/FastAPI/React must score higher than job requiring Golang/Rust/COBOL."""
        job_matched = {
            "title": "Software Developer",
            "description": "Requires Python, FastAPI, and React experience."
        }

        job_unmatched = {
            "title": "Software Developer",
            "description": "Requires Java, C++, and Angular experience."
        }

        res_matched = score_locally(
            resume_skills=self.candidate_skills,
            job_title=job_matched["title"],
            job_description=job_matched["description"]
        )

        res_unmatched = score_locally(
            resume_skills=self.candidate_skills,
            job_title=job_unmatched["title"],
            job_description=job_unmatched["description"]
        )

        print(f"Matched Stack Score:   {res_matched['score']}% (Skill Score: {res_matched['skill_score']}%, Matched: {res_matched['matched_skills']})")
        print(f"Unmatched Stack Score: {res_unmatched['score']}% (Skill Score: {res_unmatched['skill_score']}%, Matched: {res_unmatched['matched_skills']})")

        self.assertGreater(
            res_matched["score"],
            res_unmatched["score"],
            "Job with matching stack (Python/FastAPI/React) did NOT score higher than job with unmatched stack!"
        )
        self.assertEqual(res_matched["skill_score"], 98)
        self.assertEqual(res_unmatched["skill_score"], 10)

if __name__ == "__main__":
    unittest.main()
