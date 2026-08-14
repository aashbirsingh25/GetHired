import os
import json
import unittest
import hashlib
from datetime import datetime, timedelta, timezone

from job_identity import generate_canonical_job_id, normalize_url, clean_company_name, clean_job_title, clean_location
from job_deduplicator import JobDeduplicator
from pipeline import execute_authoritative_pipeline
from application_tracker import ApplicationTracker
from hybrid_scorer import HybridJobScorer
from vector_store import VectorStoreService
from embedding_service import EmbeddingService

SCRATCH_DIR = os.path.join(os.path.dirname(__file__), "scratch")

class GetHiredRegressionTestSuite(unittest.TestCase):

    def setUp(self):
        self.deduplicator = JobDeduplicator()
        os.makedirs(SCRATCH_DIR, exist_ok=True)

    def test_01_duplicate_source(self):
        """TEST 1: Same job from Indeed + career page -> 1 displayed job."""
        job_indeed = {
            "company": "Microsoft",
            "title": "Software Engineer",
            "location": "Bangalore",
            "url": "https://www.indeed.com/viewjob?jk=abc12345&utm_source=indeed",
            "source": "indeed",
            "description": "Software Engineer job at Microsoft in Bangalore."
        }
        job_career = {
            "company": "Microsoft India",
            "title": "Software Engineer",
            "location": "Bengaluru",
            "url": "https://careers.microsoft.com/us/en/job/12345/Software-Engineer",
            "source": "career_page",
            "description": "Microsoft Software Engineer posting in Bengaluru."
        }

        deduped, metrics = self.deduplicator.deduplicate([job_indeed, job_career])
        self.assertEqual(len(deduped), 1, f"Expected 1 job, got {len(deduped)}")
        self.assertEqual(metrics["duplicates_removed"], 1)
        self.assertIn("indeed", deduped[0]["sources"])
        self.assertIn("career_page", deduped[0]["sources"])

    def test_02_duplicate_url_tracking_parameters(self):
        """TEST 2: Duplicate URL with tracking parameters -> 1 displayed job."""
        url1 = "https://flipkartcareers.com/job/detail/101?utm_source=linkedin&utm_campaign=hiring&ref=123"
        url2 = "https://flipkartcareers.com/job/detail/101?utm_medium=cpc&session_id=xyz987"

        job1 = {"company": "Flipkart", "title": "Backend Developer", "location": "Bangalore", "url": url1}
        job2 = {"company": "Flipkart", "title": "Backend Developer", "location": "Bangalore", "url": url2}

        cid1 = generate_canonical_job_id(job1)
        cid2 = generate_canonical_job_id(job2)
        self.assertEqual(cid1, cid2, "Canonical job IDs must match after URL normalization")

        deduped, _ = self.deduplicator.deduplicate([job1, job2])
        self.assertEqual(len(deduped), 1)

    def test_03_same_job_across_two_scans(self):
        """TEST 3: Same company/title/location across two scans -> 1 job."""
        scan1_job = {"company": "Razorpay", "title": "Frontend Engineer", "location": "Gurugram", "url": "https://razorpay.com/jobs/1"}
        scan2_job = {"company": "Razorpay", "title": "Frontend Engineer", "location": "Gurugram", "url": "https://razorpay.com/jobs/1"}

        deduped, _ = self.deduplicator.deduplicate([scan1_job, scan2_job])
        self.assertEqual(len(deduped), 1)

    def test_04_new_resume_invalidation(self):
        """TEST 4: New resume upload invalidates/recomputes old scores."""
        resume_a = {"has_resume": True, "version_hash": "hash_aaa", "raw_text": "Python FastAPI Developer", "skills": ["Python", "FastAPI"]}
        resume_b = {"has_resume": True, "version_hash": "hash_bbb", "raw_text": "Mechanical CAD Designer", "skills": ["CAD", "SolidWorks"]}

        job = {
            "id": "job_test_04",
            "company": "Tech Corp",
            "title": "Python Developer",
            "location": "Remote",
            "description": "Python FastAPI backend engineer role",
            "match": {"score": 90, "resume_version_hash": "hash_aaa"}
        }

        scorer_b = HybridJobScorer(resume_b)
        rescore = scorer_b.score_job(job)

        self.assertEqual(rescore["resume_version_hash"], "hash_bbb")

    def test_05_strict_filtering(self):
        """TEST 5: Filter change (Role + Location) strict enforcement."""
        jobs = [
            {"title": "Software Engineer", "location": "Gurugram", "url": "https://a.com/1", "first_seen": datetime.now().isoformat()},
            {"title": "Mechanical Engineer", "location": "Mumbai", "url": "https://a.com/2", "first_seen": datetime.now().isoformat()}
        ]

        filters = {
            "target_role": ["Software Engineer"],
            "target_location": ["Gurugram"],
            "exclude_keywords": ["Senior"],
            "upload_time_hours": 24
        }

        result = execute_authoritative_pipeline(jobs, custom_filters=filters)
        filtered_jobs = result["jobs"]

        self.assertEqual(len(filtered_jobs), 1)
        self.assertEqual(filtered_jobs[0]["title"], "Software Engineer")

    def test_06_applied_state_explicit(self):
        """TEST 6: Applied state created only on explicit apply action."""
        test_file = os.path.join(SCRATCH_DIR, "test_apps_06.json")
        if os.path.exists(test_file):
            os.remove(test_file)

        tracker = ApplicationTracker(filepath=test_file)
        job_id = "canonical_test_job_06"

        apps = tracker.list_applications()
        self.assertFalse(any(a["job_id"] == job_id for a in apps))

        tracker.create_application(job_id=job_id, company="Test Co", job_title="Dev", location="Gurugram")
        
        apps_after = tracker.list_applications()
        app_match = [a for a in apps_after if a["job_id"] == job_id]
        self.assertEqual(len(app_match), 1)

    def test_07_applied_duplicate_resolution(self):
        """TEST 7: Same canonical job from two sources -> 1 application record."""
        test_file = os.path.join(SCRATCH_DIR, "test_apps_07.json")
        if os.path.exists(test_file):
            os.remove(test_file)

        job_indeed = {"company": "Google", "title": "Site Reliability Engineer", "location": "Bangalore", "url": "https://indeed.com/g1"}
        job_career = {"company": "Google India", "title": "Site Reliability Engineer", "location": "Bengaluru", "url": "https://careers.google.com/g1"}

        deduped, _ = self.deduplicator.deduplicate([job_indeed, job_career])
        canon_id = deduped[0]["id"]

        tracker = ApplicationTracker(filepath=test_file)
        rec1 = tracker.create_application(job_id=canon_id, company="Google", job_title="SRE", location="Bangalore")
        rec2 = tracker.create_application(job_id=canon_id, company="Google", job_title="SRE", location="Bangalore")

        self.assertEqual(rec1["id"], rec2["id"])

    def test_08_stale_job_recency(self):
        """TEST 8: Job older than configured window excluded from feed."""
        old_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        stale_job = {"id": "stale_1", "title": "Software Engineer", "location": "Gurugram", "url": "https://old.com/1", "first_seen": old_date}

        from recency_filter import filter_by_recency
        res = filter_by_recency([stale_job], max_hours=24)
        self.assertEqual(len(res), 0)

    def test_09_resume_relevance_contrast(self):
        """TEST 9: Strong matching vs unrelated job score contrast."""
        resume = {
            "has_resume": True,
            "version_hash": "backend_hash",
            "skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "REST API"],
            "raw_text": "Experienced Python Backend Software Engineer building FastAPI microservices and PostgreSQL databases."
        }

        # Build vector store for contrast test
        vs = VectorStoreService(
            index_path=os.path.join(SCRATCH_DIR, "test_vs.index"),
            store_path=os.path.join(SCRATCH_DIR, "test_vs.store")
        )
        vs.clear()
        embedder = EmbeddingService()
        emb = embedder.get_embedding(resume["raw_text"])
        vs.add_embeddings([emb], [{"content": resume["raw_text"], "version_hash": "backend_hash"}])

        scorer = HybridJobScorer(resume, vector_store=vs)

        good_job = {"company": "Tech", "title": "Python Backend Engineer", "description": "Looking for Python FastAPI microservices developer with PostgreSQL."}
        bad_job = {"company": "Auto", "title": "Mechanical Design Engineer", "description": "SolidWorks 3D CAD modeling, HVAC piping layout."}

        score_good = scorer.score_job(good_job)["score"]
        score_bad = scorer.score_job(bad_job)["score"]

        self.assertGreater(score_good, score_bad)
        self.assertGreater(score_good - score_bad, 20)

    def test_10_refresh_idempotency(self):
        """TEST 10: 10x refresh -> same canonical jobs, zero duplicates."""
        job = {"company": "Flipkart", "title": "Backend Lead", "location": "Bangalore", "url": "https://flipkart.com/jobs/10"}
        
        feed = [job]
        for _ in range(10):
            deduped, _ = self.deduplicator.deduplicate(feed)
            feed = deduped

        self.assertEqual(len(feed), 1)

    def test_11_background_manual_search_concurrency(self):
        """TEST 11: Background & manual search results merge deterministically."""
        bg_job = {"company": "Swiggy", "title": "Backend Dev", "location": "Bangalore", "url": "https://swiggy.com/1"}
        manual_job = {"company": "Swiggy", "title": "Backend Dev", "location": "Bangalore", "url": "https://swiggy.com/1?src=manual"}

        merged, _ = self.deduplicator.deduplicate([bg_job, manual_job])
        self.assertEqual(len(merged), 1)

    def test_12_empty_result_on_strict_filters(self):
        """TEST 12: Extremely restrictive filters -> empty feed, no stale job leaks."""
        job = {"id": "j1", "company": "Co", "title": "Junior Python Dev", "location": "Delhi", "url": "https://co.com/1", "first_seen": datetime.now().isoformat()}

        filters = {
            "target_role": ["Quantum Computing Architect"],
            "target_location": ["Antarctica"],
            "min_match_score": 99
        }

        res = execute_authoritative_pipeline([job], custom_filters=filters)
        self.assertEqual(len(res["jobs"]), 0)

if __name__ == "__main__":
    unittest.main()
