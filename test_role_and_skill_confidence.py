import os
import sys
import unittest

PROJECT_ROOT = r"c:\Users\Aashbir\OneDrive\Desktop\GetHired"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from local_scorer import score_locally, classify_role
from hybrid_semantic_fallback import score_with_hybrid_semantic

class TestRoleAndSkillConfidence(unittest.TestCase):

    def setUp(self):
        self.resume_skills = ["Python", "FastAPI", "React", "Docker", "PostgreSQL", "SQL"]

    def test_01_role_classification_hierarchy(self):
        """Verify role classification prioritizes specific phrase patterns over generic words."""
        test_cases = [
            ("Software Engineer", 90, "core_software"),
            ("Backend Engineer", 90, "core_software"),
            ("Full Stack Developer", 90, "core_software"),
            ("AI Engineer", 90, "ai_ml"),
            ("DevOps Engineer", 75, "adjacent_engineering"),
            ("Technical Support Engineer", 45, "support"),
            ("Application Support Engineer", 45, "support"),
            ("Product Support Engineer", 45, "support"),
            ("Customer Support Engineer", 45, "support"),
            ("Marketing Manager", 15, "non_technical"),
            ("Operations Manager", 15, "non_technical"),
        ]

        for title, expected_score, expected_category in test_cases:
            score, category = classify_role(title, "")
            self.assertEqual(
                score, expected_score,
                f"Title '{title}' got role_score={score}, expected {expected_score}!"
            )
            self.assertEqual(
                category, expected_category,
                f"Title '{title}' got category='{category}', expected '{expected_category}'!"
            )

    def test_02_support_engineer_not_classified_as_core_software(self):
        """Crucial test: 'Technical Support Engineer' must NOT get 90% role score."""
        score, category = classify_role("Technical Support Engineer", "Providing customer technical support")
        self.assertEqual(category, "support")
        self.assertEqual(score, 45)

    def test_03_skill_confidence_states(self):
        """Verify 3-state skill confidence model (explicit, inferred, unknown)."""
        # A. 3/3 matched skills -> explicit, 98%
        res_3_matched = score_locally(
            self.resume_skills,
            "Backend Developer",
            "Looking for Python, FastAPI, and React skills."
        )
        self.assertEqual(res_3_matched["skill_confidence"], "explicit")
        self.assertEqual(res_3_matched["skill_score"], 98)

        # B. 3/7 extracted tech skills matched -> explicit, 43%
        res_3_of_7 = score_locally(
            self.resume_skills,
            "Software Developer",
            "Requires Python, FastAPI, React, Java, C++, Angular, Kubernetes, Spring Boot."
        )
        self.assertEqual(res_3_of_7["skill_confidence"], "explicit")
        self.assertEqual(res_3_of_7["skill_score"], 43)

        # C. 1/1 extracted skill in short text -> explicit, 98%
        res_short_1 = score_locally(
            self.resume_skills,
            "Python Developer",
            "Python dev needed."  # Short desc <= 300 chars
        )
        self.assertEqual(res_short_1["skill_confidence"], "explicit")
        self.assertEqual(res_short_1["skill_score"], 98)

        # D. 1 skill extracted in long description (> 300 chars) -> inferred, 65%
        long_desc = "Seeking a Database Reliability Engineer. " + ("Detailed responsibilities, backup recovery, SLA monitoring. " * 15) + "Experience with SQL required."
        res_long_1 = score_locally(
            self.resume_skills,
            "Database Reliability Engineer",
            long_desc
        )
        self.assertEqual(res_long_1["skill_confidence"], "inferred")
        self.assertEqual(res_long_1["skill_score"], 65)

        # E. Completely unmatched stack -> explicit, 10%
        res_unmatched = score_locally(
            self.resume_skills,
            "Java Developer",
            "Requires Java and Spring Boot experience."
        )
        self.assertEqual(res_unmatched["skill_confidence"], "explicit")
        self.assertEqual(res_unmatched["skill_score"], 10)

        # F. Title-only job / missing description -> unknown, skill_score = None
        res_title_only = score_locally(
            self.resume_skills,
            "Software Engineer",
            ""  # empty description
        )
        self.assertEqual(res_title_only["skill_confidence"], "unknown")
        self.assertIsNone(res_title_only["skill_score"])

if __name__ == "__main__":
    unittest.main()
