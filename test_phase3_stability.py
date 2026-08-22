import os
import sys
import json
import time
import unittest

BASE_DIR = os.path.dirname(__file__)
sys.path.insert(0, BASE_DIR)

class TestPhase3Stability(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from app import bg_worker
        bg_worker.config["enabled"] = False
        bg_worker.stop()

    def setUp(self):
        self.backup_files = [
            "jobs_store.json", "jobs_curated.json", "resume_store.json",
            "config.json", "companies.json", "filters.json", "metrics.json"
        ]
        self.backups = {}
        self.missing_before = []
        for fn in self.backup_files:
            fp = os.path.join(BASE_DIR, fn)
            if os.path.exists(fp):
                with open(fp, "r", encoding="utf-8") as f:
                    self.backups[fn] = f.read()
            else:
                self.missing_before.append(fn)

    def tearDown(self):
        for fn, content in self.backups.items():
            fp = os.path.join(BASE_DIR, fn)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(content)
        # Files that did not exist before the test must not survive it —
        # otherwise test fixtures leak into the real production store.
        for fn in self.missing_before:
            fp = os.path.join(BASE_DIR, fn)
            if os.path.exists(fp):
                os.remove(fp)

    def test_01_null_match_safety_in_test_config(self):
        from app import app
        with app.test_client() as client:
            # Test when payload has min_match_score
            res = client.post("/api/background-search/test-config", data=json.dumps({"min_match_score": 60}), content_type="application/json")
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertIn("would_match_count", data)
            self.assertIn("total_evaluated", data)

    def test_02_async_jobs_search_returns_immediately(self):
        from app import app
        with app.test_client() as client:
            t0 = time.time()
            res = client.post("/api/jobs/search", data=json.dumps({"roles": ["Software Engineer"]}), content_type="application/json")
            duration = time.time() - t0
            self.assertLess(duration, 2.0, "POST /api/jobs/search must return immediately (<2s)")
            self.assertIn(res.status_code, [202, 409])
            data = res.get_json()
            self.assertIn("status", data)
            self.assertIn("task_id", data)

            # Test status endpoint
            task_id = data["task_id"]
            st_res = client.get(f"/api/jobs/search/status/{task_id}")
            self.assertEqual(st_res.status_code, 200)
            st_data = st_res.get_json()
            self.assertEqual(st_data["task_id"], task_id)

    def test_03_get_jobs_does_not_synchronously_rescore(self):
        from pipeline import execute_authoritative_pipeline
        raw_jobs = [
            {"id": "j1", "title": "Software Engineer", "company": "Co A", "location": "Gurugram", "match": None},
            {"id": "j2", "title": "Developer", "company": "Co B", "location": "Delhi", "match": "invalid_string"},
            {"id": "j3", "title": "Backend Dev", "company": "Co C", "location": "Bangalore", "match": {"score": 85}}
        ]
        t0 = time.time()
        res = execute_authoritative_pipeline(raw_jobs=raw_jobs, custom_filters={"min_match_score": 0}, resume_data={"has_resume": True, "version_hash": "v99"})
        duration = time.time() - t0
        self.assertLess(duration, 0.5, "execute_authoritative_pipeline must not execute synchronous heavy scoring")
        # Non-blocking design preserved: no synchronous scoring is done here.
        # j3 has a real cached score -> ranked feed. j1 (no match) and j2
        # (invalid match) are unscored -> held out of feed as PENDING (option a).
        feed = res["jobs"]
        pending = res["pending_jobs"]
        feed_map = {j["title"]: j for j in feed}
        pending_titles = [j["title"] for j in pending]
        self.assertEqual(feed_map["Backend Dev"]["match"]["score"], 85)
        self.assertNotIn("Software Engineer", feed_map)
        self.assertNotIn("Developer", feed_map)
        self.assertIn("Software Engineer", pending_titles)
        self.assertIn("Developer", pending_titles)
        for p in pending:
            self.assertEqual(p["match"]["match_grade"], "PENDING")

    def test_04_rescore_status_tracking(self):
        import tempfile
        from unittest.mock import MagicMock, patch
        import app as app_module
        from app import _async_rescore_jobs, load_json, save_json

        # Never write test fixtures to the real data stores — redirect the
        # module-level file paths to a temp directory for this test.
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_jobs = os.path.join(tmpdir, "jobs_store.json")
            tmp_resume = os.path.join(tmpdir, "resume_store.json")
            with patch.object(app_module, "JOBS_FILE", tmp_jobs), \
                 patch.object(app_module, "RESUME_FILE", tmp_resume):
                save_json(tmp_jobs, {
                    "jobs": [
                        {"id": "j1", "title": "Python Dev", "company": "Co A", "location": "Gurugram"},
                        {"id": "j2", "title": "Flask Dev", "company": "Co B", "location": "Delhi"}
                    ]
                })
                sample_resume = {
                    "has_resume": True,
                    "skills": ["Python", "Flask"],
                    "version_hash": "test_v123",
                    "raw_text": "Sample text"
                }
                mock_scorer_instance = MagicMock()
                mock_scorer_instance.score_job.return_value = {"score": 80, "match_grade": "STRONG_MATCH"}
                with patch("app.HybridJobScorer", return_value=mock_scorer_instance):
                    _async_rescore_jobs(sample_resume)

                r_store = load_json(tmp_resume, {})
        self.assertIn("rescore_status", r_store)
        st = r_store["rescore_status"]
        self.assertEqual(st["status"], "completed")
        self.assertEqual(st["total_jobs"], 2)
        self.assertEqual(st["scored_jobs"], 2)
        self.assertIsNotNone(st["started_at"])
        self.assertIsNotNone(st["completed_at"])
        self.assertIsNone(st["error"])

    def test_05_atomic_json_write_preservation(self):
        from scan_coordinator import save_json
        test_file = os.path.join(BASE_DIR, "scratch", "test_atomic.json")
        original_data = {"key": "original_value", "number": 42}
        save_json(test_file, original_data)

        # Verify initial write
        with open(test_file, "r", encoding="utf-8") as f:
            read_data = json.load(f)
        self.assertEqual(read_data, original_data)

        # Verify that no temporary files remain
        tmp_files = [f for f in os.listdir(os.path.join(BASE_DIR, "scratch")) if f.startswith("test_atomic.json.tmp")]
        self.assertEqual(len(tmp_files), 0)

        # Cleanup
        if os.path.exists(test_file):
            os.remove(test_file)

    def test_06_background_worker_duplicate_thread_protection(self):
        from background_search_worker import BackgroundSearchWorker
        bg = BackgroundSearchWorker()
        bg.is_currently_searching = True
        
        # Test update_config when already searching
        bg.update_config({"enabled": True, "interval_hours": 2})
        # Test execute_search_cycle when already searching
        res = bg.execute_search_cycle()
        self.assertIn("jobs", res)
        # Verify lock state remained True and wasn't double-spawned
        self.assertTrue(bg.is_currently_searching)

    def test_07_score_consensus_malformed_input_handling(self):
        from score_consensus_checker import extract_numeric_score, should_verify, verify_with_second_opinion
        
        self.assertEqual(extract_numeric_score(None, 50), 50)
        self.assertEqual(extract_numeric_score({"score": 75}, 50), 75)
        self.assertEqual(extract_numeric_score({"score": None}, 50), 50)
        self.assertEqual(extract_numeric_score("82", 50), 82)
        self.assertEqual(extract_numeric_score("invalid", 50), 50)
        self.assertEqual(extract_numeric_score(45.6, 50), 45)

        # Verify consensus with malformed primary score
        res = verify_with_second_opinion(
            resume_chunks=["chunk"],
            resume_skills=["Python"],
            job_title="Software Engineer",
            job_description="Python developer needed",
            primary_score={"score": 50},
            primary_source="local",
            primary_tier=6
        )
        self.assertIn("consensus", res)

    def test_08_source_health_reporting(self):
        from fetchers.indeed_fetcher import fetch_indeed_jobs, JobFetcherList
        from fetchers.naukri_fetcher import fetch_naukri_jobs

        indeed_jobs = fetch_indeed_jobs(role="Software Engineer", location="Gurugram")
        self.assertIsInstance(indeed_jobs, list)
        self.assertTrue(hasattr(indeed_jobs, "source_health"))
        self.assertIn(indeed_jobs.source_health["status"], ["unconfigured", "blocked", "unavailable", "zero_results", "success"])

        naukri_meta = fetch_naukri_jobs(role="Software Engineer", location="Gurugram", return_metadata=True)
        self.assertIn("status", naukri_meta)
        self.assertIn("health", naukri_meta)
        self.assertIn("jobs", naukri_meta)

    def test_09_interactive_task_history_preservation(self):
        from background_search_worker import BackgroundSearchWorker
        bg = BackgroundSearchWorker()
        bg.is_currently_searching = False
        t1 = bg.trigger_interactive_search(task_id="task_001")
        bg.is_currently_searching = False
        t2 = bg.trigger_interactive_search(task_id="task_002")

        st1 = bg.get_interactive_search_status("task_001")
        st2 = bg.get_interactive_search_status("task_002")
        self.assertEqual(st1["task_id"], "task_001")
        self.assertEqual(st2["task_id"], "task_002")

    def test_10_pending_jobs_held_out_of_feed_not_bypassing_min_score(self):
        from pipeline import execute_authoritative_pipeline
        raw_jobs = [
            {"id": "j1", "title": "Software Engineer", "company": "Co A", "location": "Gurugram", "match": None},
            {"id": "j2", "title": "Full Stack Engineer", "company": "Co B", "location": "Gurugram",
             "match": {"score": 85, "resume_version_hash": "vX"}}
        ]
        res = execute_authoritative_pipeline(
            raw_jobs=raw_jobs, custom_filters={"min_match_score": 60},
            resume_data={"has_resume": True, "version_hash": "vX"}
        )
        feed_titles = [j["title"] for j in res["jobs"]]
        pending_titles = [j["title"] for j in res["pending_jobs"]]
        # j2 (scored 85 >= 60) belongs in the ranked feed.
        self.assertIn("Full Stack Engineer", feed_titles)
        # j1 (unscored/PENDING) must NOT be in the feed and must NOT bypass
        # min_match_score; it is surfaced separately for the "still scoring" indicator.
        self.assertNotIn("Software Engineer", feed_titles)
        self.assertIn("Software Engineer", pending_titles)
        self.assertEqual(res["metrics"]["pending"], 1)

    def test_10b_stale_resume_hash_marks_job_pending(self):
        from pipeline import execute_authoritative_pipeline
        raw_jobs = [
            # Cached score, but from an OLD resume version.
            {"id": "j1", "title": "Software Engineer", "company": "Co A", "location": "Gurugram",
             "match": {"score": 90, "match_grade": "STRONG_MATCH", "resume_version_hash": "old_hash"}},
            # Cached score matching the CURRENT resume version.
            {"id": "j2", "title": "Backend Engineer", "company": "Co B", "location": "Gurugram",
             "match": {"score": 80, "match_grade": "STRONG_MATCH", "resume_version_hash": "new_hash"}}
        ]
        res = execute_authoritative_pipeline(
            raw_jobs=raw_jobs,
            custom_filters={"min_match_score": 0},
            resume_data={"has_resume": True, "version_hash": "new_hash"}
        )
        # Dedup canonicalizes ids, so match on title.
        feed_titles = [j["title"] for j in res["jobs"]]
        pending_titles = [j["title"] for j in res["pending_jobs"]]
        # Stale-hash job (old_hash != new_hash) must be treated as PENDING.
        self.assertIn("Software Engineer", pending_titles)
        self.assertNotIn("Software Engineer", feed_titles)
        # Current-hash job stays in feed with its real score.
        self.assertIn("Backend Engineer", feed_titles)
        feed_map = {j["title"]: j for j in res["jobs"]}
        self.assertEqual(feed_map["Backend Engineer"]["match"]["score"], 80)

    def test_11_score_consensus_clamping(self):
        from score_consensus_checker import extract_numeric_score
        self.assertEqual(extract_numeric_score(150), 100)
        self.assertEqual(extract_numeric_score(-20), 0)
        self.assertEqual(extract_numeric_score("120"), 100)

if __name__ == "__main__":
    unittest.main()
