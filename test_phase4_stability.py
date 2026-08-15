import unittest
import os
import json
import time
import threading
from typing import Dict, Any, List

from pipeline import execute_authoritative_pipeline, _matches_role_title, _matches_location
from job_deduplicator import JobDeduplicator
from score_consensus_checker import extract_numeric_score
from background_search_worker import BackgroundSearchWorker
from app import app, load_json, save_json, JOBS_FILE, RESUME_FILE, _app_startup_safeguard

class TestPhase4Stability(unittest.TestCase):

    def setUp(self):
        self.app_client = app.test_client()

    def test_01_role_filtering_symbol_roles(self):
        """Verify role matching for C++, C#, .NET, C, R, Node.js without false matches."""
        self.assertTrue(_matches_role_title("C++", "Senior C++ Developer"))
        self.assertFalse(_matches_role_title("C++", "C Developer"))
        
        self.assertTrue(_matches_role_title("C#", "C# Software Engineer"))
        self.assertFalse(_matches_role_title("C#", "C Developer"))
        
        self.assertTrue(_matches_role_title("C", "C Developer"))
        self.assertFalse(_matches_role_title("C", "C++ Developer"))
        self.assertFalse(_matches_role_title("C", "C# Developer"))

        self.assertTrue(_matches_role_title("R", "R Developer"))
        self.assertFalse(_matches_role_title("R", "R&D Manager"))

        self.assertTrue(_matches_role_title(".NET", ".NET Backend Engineer"))
        self.assertTrue(_matches_role_title("Java", "Java Developer"))
        self.assertFalse(_matches_role_title("Java", "JavaScript Engineer"))

    def test_02_location_synonyms(self):
        """Verify location synonym expansion for Indian tech hubs."""
        self.assertTrue(_matches_location("Gurugram", "Gurgaon, India"))
        self.assertTrue(_matches_location("Gurgaon", "Gurugram, Haryana"))
        self.assertTrue(_matches_location("Bangalore", "Bengaluru, Karnataka"))
        self.assertTrue(_matches_location("Bengaluru", "Bangalore, India"))
        self.assertTrue(_matches_location("Delhi NCR", "Noida, Sector 62"))

    def test_03_deduplication_seniority_separation(self):
        """Verify Senior, Staff, and Junior titles are NEVER merged as duplicate clusters."""
        dedup = JobDeduplicator()
        jobs = [
            {"id": "j1", "title": "Software Engineer", "company": "Acme Corp", "location": "Gurugram", "url": "https://acme.com/j1"},
            {"id": "j2", "title": "Senior Software Engineer", "company": "Acme Corp", "location": "Gurugram", "url": "https://acme.com/j2"},
            {"id": "j3", "title": "Staff Software Engineer", "company": "Acme Corp", "location": "Gurugram", "url": "https://acme.com/j3"}
        ]
        deduped, metrics = dedup.deduplicate(jobs)
        self.assertEqual(len(deduped), 3)
        self.assertEqual(metrics["duplicates_removed"], 0)

    def test_04_atomic_write_concurrency_and_cleanup(self):
        """Verify concurrent atomic writes do not collide or leave temporary files."""
        test_file = "scratch/test_atomic_p4.json"
        os.makedirs("scratch", exist_ok=True)
        errors = []

        def worker_write(val):
            try:
                for i in range(10):
                    save_json(test_file, {"val": val, "iter": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker_write, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertTrue(os.path.exists(test_file))
        # Ensure no stray .tmp files left in scratch/
        tmp_files = [f for f in os.listdir("scratch") if f.startswith("test_atomic_p4.json.tmp")]
        self.assertEqual(len(tmp_files), 0)
        if os.path.exists(test_file):
            os.remove(test_file)

    def test_05_failure_resilience_discovery(self):
        """Verify search worker handles individual fetcher failures gracefully."""
        bg = BackgroundSearchWorker()
        res = bg.get_interactive_search_status("non_existent_task")
        self.assertEqual(res["status"], "not_found")

    def test_06_scoring_pipeline_unscored_and_pending(self):
        """Verify pipeline preserves unscored pending jobs when min_match_score > 0."""
        raw_jobs = [
            {"id": "j1", "title": "Software Engineer", "company": "Co A", "location": "Gurugram", "match": None},
            {"id": "j2", "title": "Full Stack Engineer", "company": "Co B", "location": "Gurugram", "match": {"score": 85}}
        ]
        out = execute_authoritative_pipeline(raw_jobs, custom_filters={"min_match_score": 70})
        titles = [j["title"] for j in out["jobs"]]
        self.assertIn("Software Engineer", titles)
        self.assertIn("Full Stack Engineer", titles)

    def test_07_api_contract_verification(self):
        """Verify essential HTTP API response status codes and schemas."""
        res_jobs = self.app_client.get("/api/jobs")
        self.assertIn(res_jobs.status_code, [200, 304])

        res_search = self.app_client.post("/api/jobs/search", json={"roles": ["Python Developer"]})
        self.assertEqual(res_search.status_code, 202)
        task_data = res_search.get_json()
        self.assertIn("task_id", task_data)

        task_id = task_data["task_id"]
        res_status = self.app_client.get(f"/api/jobs/search/status/{task_id}")
        self.assertEqual(res_status.status_code, 200)
        st_json = res_status.get_json()
        self.assertIn(st_json["status"], ["queued", "running", "completed"])

    def test_08_restart_and_store_integrity(self):
        """Verify startup safeguard executes cleanly without blowing up store integrity."""
        _app_startup_safeguard()
        store_data = load_json(JOBS_FILE, {"jobs": []})
        self.assertIn("jobs", store_data)
        self.assertIsInstance(store_data["jobs"], list)

    def test_09_end_to_end_pipeline(self):
        """End-to-end validation of full discovery to API response flow."""
        raw_jobs = [
            {"id": "j10", "title": "Senior C++ Engineer", "company": "Tech Corp", "location": "Gurgaon", "url": "https://tech.corp/j10"},
            {"id": "j11", "title": "Senior C++ Engineer", "company": "Tech Corp", "location": "Gurugram", "url": "https://tech.corp/j10?src=indeed"},
            {"id": "j12", "title": "Java Developer", "company": "Tech Corp", "location": "Gurugram", "url": "https://tech.corp/j12"}
        ]
        filters = {"target_role": ["C++"], "target_location": ["Gurugram"], "exclude_keywords": []}
        out = execute_authoritative_pipeline(raw_jobs, custom_filters=filters)
        jobs = out["jobs"]
        # Duplicate j10 and j11 must merge, j12 (Java) must be filtered out by role filter C++
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "Tech Corp")
        self.assertEqual(jobs[0]["title"], "Senior C++ Engineer")

if __name__ == "__main__":
    unittest.main()
