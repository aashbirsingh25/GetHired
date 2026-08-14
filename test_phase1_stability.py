import os
import json
import unittest
from datetime import datetime, timezone
from flask import Flask

from app import app, parse_datetime_safely, RESUME_FILE, JOBS_FILE
from pipeline import execute_authoritative_pipeline

class Phase1StabilityRegressionTestSuite(unittest.TestCase):

    def setUp(self):
        self.app_client = app.test_client()
        app.config['TESTING'] = True

    def test_01_get_jobs_does_not_mutate_store(self):
        """Test GET /api/jobs does NOT write to jobs_store.json on disk."""
        if os.path.exists(JOBS_FILE):
            with open(JOBS_FILE, "r", encoding="utf-8") as f:
                mtime_before = os.path.getmtime(JOBS_FILE)
        else:
            mtime_before = 0

        response = self.app_client.get("/api/jobs")
        self.assertEqual(response.status_code, 200)

        if os.path.exists(JOBS_FILE):
            mtime_after = os.path.getmtime(JOBS_FILE)
            self.assertEqual(mtime_before, mtime_after, "GET /api/jobs must NOT mutate jobs_store.json on disk")

    def test_02_role_filter_word_boundaries(self):
        """Test Role Filter enforces word boundaries (e.g. Java != JavaScript, art != Partner)."""
        jobs = [
            {"id": "j1", "title": "JavaScript Frontend Developer", "location": "Gurugram", "url": "https://a.com/1", "first_seen": datetime.now(timezone.utc).isoformat()},
            {"id": "j2", "title": "Java Backend Engineer", "location": "Gurugram", "url": "https://a.com/2", "first_seen": datetime.now(timezone.utc).isoformat()},
            {"id": "j3", "title": "Senior Partner Manager", "location": "Gurugram", "url": "https://a.com/3", "first_seen": datetime.now(timezone.utc).isoformat()},
            {"id": "j4", "title": "Software Engineering Intern", "location": "Gurugram", "url": "https://a.com/4", "first_seen": datetime.now(timezone.utc).isoformat()}
        ]

        filters_java = {"target_role": ["Java"], "target_location": ["Gurugram"], "exclude_keywords": []}
        res_java = execute_authoritative_pipeline(jobs, custom_filters=filters_java)
        titles_java = [j["title"] for j in res_java["jobs"]]
        self.assertNotIn("JavaScript Frontend Developer", titles_java, "'Java' must not match 'JavaScript'")
        self.assertIn("Java Backend Engineer", titles_java, "'Java' must match 'Java Backend Engineer'")

        filters_art = {"target_role": ["art"], "target_location": ["Gurugram"], "exclude_keywords": []}
        res_art = execute_authoritative_pipeline(jobs, custom_filters=filters_art)
        titles_art = [j["title"] for j in res_art["jobs"]]
        self.assertNotIn("Senior Partner Manager", titles_art, "'art' must not match 'Partner'")

        filters_in = {"target_role": ["in"], "target_location": ["Gurugram"], "exclude_keywords": []}
        res_in = execute_authoritative_pipeline(jobs, custom_filters=filters_in)
        titles_in = [j["title"] for j in res_in["jobs"]]
        self.assertNotIn("Software Engineering Intern", titles_in, "'in' must not match 'Intern'")

    def test_03_location_filter_structured_field_only(self):
        """Test Location Filter uses structured location field, not arbitrary description text."""
        jobs = [
            {
                "id": "j_us",
                "title": "Software Engineer",
                "location": "Austin, TX",
                "description": "Our company has major operational hubs across India, Bangalore, and Delhi.",
                "url": "https://a.com/us",
                "first_seen": datetime.now(timezone.utc).isoformat()
            },
            {
                "id": "j_ind",
                "title": "Software Engineer",
                "location": "Gurugram, India",
                "description": "Software engineer role in Gurugram.",
                "url": "https://a.com/ind",
                "first_seen": datetime.now(timezone.utc).isoformat()
            },
            {
                "id": "j_rem",
                "title": "Backend Engineer",
                "location": "Remote",
                "description": "Full remote position anywhere in India.",
                "url": "https://a.com/rem",
                "first_seen": datetime.now(timezone.utc).isoformat()
            }
        ]

        filters_gurugram = {"target_role": ["Software Engineer"], "target_location": ["Gurugram"], "exclude_keywords": []}
        res = execute_authoritative_pipeline(jobs, custom_filters=filters_gurugram)
        matched_locations = [j["location"] for j in res["jobs"]]

        self.assertEqual(len(res["jobs"]), 1, "Only the Gurugram India job should match")
        self.assertIn("Gurugram, India", matched_locations, "Gurugram India job MUST match 'Gurugram' location filter")

    def test_04_daily_digest_date_parsing(self):
        """Test parse_datetime_safely handles valid ISO, custom formats, and malformed strings gracefully."""
        d1 = parse_datetime_safely("2026-08-14T12:00:00Z")
        self.assertIsNotNone(d1)

        d2 = parse_datetime_safely("2026-08-14 17:00:00")
        self.assertIsNotNone(d2)

        d3 = parse_datetime_safely("Wed, 14 Aug 2026 12:00:00 GMT")
        self.assertIsNotNone(d3)

        d_invalid = parse_datetime_safely("invalid_malformed_date_string")
        self.assertIsNone(d_invalid, "Malformed dates must return None instead of throwing Exception")

        # Test daily-digest endpoint response with test client
        resp = self.app_client.get("/api/daily-digest")
        self.assertEqual(resp.status_code, 200)

    def test_05_special_char_and_multiword_roles(self):
        """Test Role Filter handles special characters like C++, C#, and multi-word roles cleanly."""
        jobs = [
            {"id": "j_cpp", "title": "Senior C++ Software Engineer", "location": "Gurugram", "url": "https://a.com/cpp", "first_seen": datetime.now(timezone.utc).isoformat()},
            {"id": "j_fs", "title": "Lead Full Stack Engineer", "location": "Gurugram", "url": "https://a.com/fs", "first_seen": datetime.now(timezone.utc).isoformat()}
        ]

        res_cpp = execute_authoritative_pipeline(jobs, custom_filters={"target_role": ["C++"], "target_location": ["Gurugram"], "exclude_keywords": []})
        self.assertEqual(len(res_cpp["jobs"]), 1)
        self.assertIn("Senior C++ Software Engineer", [j["title"] for j in res_cpp["jobs"]])

        res_fs = execute_authoritative_pipeline(jobs, custom_filters={"target_role": ["Full Stack Engineer"], "target_location": ["Gurugram"], "exclude_keywords": []})
        self.assertEqual(len(res_fs["jobs"]), 1)
        self.assertIn("Lead Full Stack Engineer", [j["title"] for j in res_fs["jobs"]])

if __name__ == "__main__":
    unittest.main()
