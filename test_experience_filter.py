"""Scoped regression tests for pipeline experience filtering (step 6)."""
import unittest
from pipeline import (
    _extract_min_experience_years,
    _max_user_experience_years,
    execute_authoritative_pipeline,
)


class TestExperienceFilter(unittest.TestCase):
    def test_extracts_range_plus_and_minimum(self):
        self.assertEqual(_extract_min_experience_years(
            {"title": "SWE", "description": "requires 3-5yrs of experience"}), 3)
        self.assertEqual(_extract_min_experience_years(
            {"title": "SWE (2+ years)", "description": ""}), 2)
        self.assertEqual(_extract_min_experience_years(
            {"title": "SWE", "description": "minimum of 4 years"}), 4)

    def test_ignores_non_requirement_years(self):
        self.assertIsNone(_extract_min_experience_years(
            {"title": "SWE", "description": "our company launched 9 years ago"}))

    def test_user_ceiling_parsing(self):
        self.assertEqual(_max_user_experience_years(
            {"target_experience": ["0-2 years", "Fresher"]}), 2)
        self.assertIsNone(_max_user_experience_years({}))

    def test_pipeline_drops_over_experienced_jobs(self):
        jobs = [
            {"id": "a", "title": "Software Engineer - Backend", "company": "AlphaCo", "location": "Bangalore",
             "description": "requires 3-5 years experience", "match": {"score": 90, "match_grade": "STRONG_MATCH"}},
            {"id": "b", "title": "Software Engineer - Platform", "company": "BetaCo", "location": "Bangalore",
             "description": "0-2 years welcome", "match": {"score": 70, "match_grade": "GOOD_MATCH"}},
        ]
        res = execute_authoritative_pipeline(
            raw_jobs=jobs,
            custom_filters={"target_experience": ["0-2 years"], "min_match_score": 0,
                            "target_role": ["Software Engineer"], "target_location": ["Bangalore"]},
            resume_data={"has_resume": False},
        )
        titles = {j["title"] for j in res["jobs"]}
        self.assertNotIn("Software Engineer - Backend", titles)   # 3-5 yrs: dropped
        self.assertIn("Software Engineer - Platform", titles)     # 0-2 yrs: kept


if __name__ == "__main__":
    unittest.main()
