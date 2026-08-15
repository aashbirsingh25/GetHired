import unittest
import os
import json
import requests
from unittest.mock import MagicMock, patch

from browser_scanner import BrowserScanner
from background_search_worker import BackgroundSearchWorker
from job_deduplicator import JobDeduplicator

class TestPhase14Stability(unittest.TestCase):

    def test_01_greenhouse_token_parsing_and_description(self):
        """Verify Greenhouse token parsing and full HTML description extraction."""
        scanner = BrowserScanner(headless=True)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "jobs": [
                {
                    "title": "React Developer",
                    "location": {"name": "Gurugram, India"},
                    "absolute_url": "https://boards.greenhouse.io/figma/jobs/101",
                    "content": "<p>We are looking for a <strong>React Developer</strong> with Python skills.</p>",
                    "updated_at": "2026-08-15"
                }
            ]
        }

        # Test URL variants
        urls = [
            "https://boards.greenhouse.io/figma",
            "https://boards.greenhouse.io/embed/job_board?for=figma",
            "https://job-boards.greenhouse.io/figma/",
            "https://boards.greenhouse.io/c/figma"
        ]

        for url in urls:
            with patch("requests.get", return_value=mock_resp) as mock_get:
                jobs = scanner._extract_greenhouse_jobs({"id": "figma", "name": "Figma", "career_url": url})
                self.assertEqual(len(jobs), 1)
                self.assertEqual(jobs[0]["title"], "React Developer")
                self.assertEqual(jobs[0]["location"], "Gurugram, India")
                self.assertIn("React Developer", jobs[0]["description"])
                self.assertIn("Python", jobs[0]["description"])
                self.assertEqual(jobs[0]["extraction_method"], "greenhouse_api")

                # Check that correct board token "figma" was requested
                called_url = mock_get.call_args[0][0]
                self.assertIn("/boards/figma/jobs", called_url)

    def test_02_lever_token_parsing_and_description(self):
        """Verify Lever token parsing and description reconstruction with lists."""
        scanner = BrowserScanner(headless=True)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {
                "text": "Full Stack Engineer",
                "categories": {"location": "Bangalore"},
                "hostedUrl": "https://jobs.lever.co/swiggy/201",
                "descriptionPlain": "Build great APIs.",
                "lists": [
                    {
                        "text": "Requirements",
                        "content": ["Python experience", "Docker skills"]
                    }
                ]
            }
        ]

        url = "https://jobs.lever.co/careers/swiggy/"
        with patch("requests.get", return_value=mock_resp) as mock_get:
            jobs = scanner._extract_lever_jobs({"id": "swiggy", "name": "Swiggy", "career_url": url})
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["title"], "Full Stack Engineer")
            self.assertIn("Build great APIs.", jobs[0]["description"])
            self.assertIn("Requirements:\nPython experience\nDocker skills", jobs[0]["description"])

            # Check site slug "swiggy" was parsed correctly
            called_url = mock_get.call_args[0][0]
            self.assertIn("/postings/swiggy", called_url)

    def test_03_ashby_token_parsing_and_description(self):
        """Verify Ashby token parsing and BeautifulSoup description handling."""
        scanner = BrowserScanner(headless=True)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "jobs": [
                {
                    "title": "Backend SDE",
                    "locationName": "Mumbai",
                    "jobUrl": "https://jobs.ashbyhq.com/notion/301",
                    "descriptionHtml": "<div>FastAPI and PostgreSQL work.</div>",
                    "publishedAt": "2026-08-05"
                }
            ]
        }

        url = "https://jobs.ashbyhq.com/notion/"
        with patch("requests.get", return_value=mock_resp) as mock_get:
            jobs = scanner._extract_ashby_jobs({"id": "notion", "name": "Notion", "career_url": url})
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["title"], "Backend SDE")
            self.assertEqual(jobs[0]["description"], "FastAPI and PostgreSQL work.")

            called_url = mock_get.call_args[0][0]
            self.assertIn("/job-board/notion", called_url)

    def test_04_smartrecruiters_detail_fetching(self):
        """Verify SmartRecruiters detail endpoint is queried to fetch real description."""
        scanner = BrowserScanner(headless=True)
        mock_list_resp = MagicMock()
        mock_list_resp.status_code = 200
        mock_list_resp.json.return_value = {
            "content": [
                {
                    "id": "sr-401",
                    "name": "DevOps Engineer",
                    "location": {"city": "Noida"},
                    "releasedDate": "2026-08-02"
                }
            ]
        }

        mock_detail_resp = MagicMock()
        mock_detail_resp.status_code = 200
        mock_detail_resp.json.return_value = {
            "jobAd": {
                "sections": {
                    "jobDescription": {"text": "Maintain CI/CD pipelines."},
                    "qualifications": {"text": "Kubernetes and AWS expertise."}
                }
            }
        }

        def side_effect(url, *args, **kwargs):
            if "postings/sr-401" in url:
                return mock_detail_resp
            return mock_list_resp

        url = "https://jobs.smartrecruiters.com/visa"
        with patch("requests.get", side_effect=side_effect) as mock_get:
            jobs = scanner._extract_smartrecruiters_jobs({"id": "visa", "name": "Visa", "career_url": url})
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["title"], "DevOps Engineer")
            self.assertIn("Maintain CI/CD pipelines.", jobs[0]["description"])
            self.assertIn("Kubernetes and AWS expertise.", jobs[0]["description"])

    def test_05_workday_detail_fetching(self):
        """Verify Workday detail endpoint is queried to fetch real description."""
        scanner = BrowserScanner(headless=True)
        mock_list_resp = MagicMock()
        mock_list_resp.status_code = 200
        mock_list_resp.json.return_value = {
            "jobPostings": [
                {
                    "title": "Software Engineer II",
                    "locationsText": "Bangalore",
                    "externalPath": "/job/R102",
                    "postedOn": "2026-08-01"
                }
            ]
        }

        mock_detail_resp = MagicMock()
        mock_detail_resp.status_code = 200
        mock_detail_resp.json.return_value = {
            "jobDescription": "<h3>Figma designs to React code.</h3>"
        }

        mock_empty_resp = MagicMock()
        mock_empty_resp.status_code = 200
        mock_empty_resp.json.return_value = {"jobPostings": []}

        post_calls = 0
        def post_side_effect(*args, **kwargs):
            nonlocal post_calls
            if post_calls == 0:
                post_calls += 1
                return mock_list_resp
            return mock_empty_resp

        def side_effect(url, *args, **kwargs):
            if "job/R102" in url:
                return mock_detail_resp
            return mock_list_resp

        url = "https://adobe.wd5.myworkdayjobs.com/external_careers"
        with patch("requests.post", side_effect=post_side_effect):
            with patch("requests.get", side_effect=side_effect):
                jobs = scanner._extract_workday_jobs({"id": "adobe", "name": "Adobe", "career_url": url})
                self.assertEqual(len(jobs), 1)
                self.assertEqual(jobs[0]["title"], "Software Engineer II")
                self.assertIn("Figma designs to React code.", jobs[0]["description"])

    def test_06_direct_ats_no_playwright_fallback(self):
        """Verify direct ATS scanning returns directly without launching browser."""
        scanner = BrowserScanner(headless=True)
        scanner.start = MagicMock() # Mock playwright launch

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"jobs": []}

        url = "https://boards.greenhouse.io/figma"
        with patch("requests.get", return_value=mock_resp):
            jobs, used_pattern, method, err = scanner.scan_company(
                {"id": "figma", "name": "Figma", "career_url": url, "ats": "greenhouse"}
            )
            # Should have returned immediately
            self.assertEqual(method, "greenhouse_api")
            self.assertFalse(scanner.start.called)

    def test_07_job_deduplicator_metadata_selection(self):
        """Verify deduplicator prefers jobs with higher parse confidence and description length."""
        deduplicator = JobDeduplicator()

        # 1. Aggregated low-confidence job
        job_low = {
            "id": "job-1",
            "company": "Swiggy",
            "title": "Software Engineer",
            "location": "Bangalore",
            "url": "https://www.indeed.com/viewjob?jk=123",
            "description": "Indeed snippet: React developer",
            "parse_confidence": 0.5,
            "source": "indeed"
        }

        # 2. Authoritative API high-confidence job
        job_high = {
            "id": "job-2",
            "company": "Swiggy",
            "title": "Software Engineer",
            "location": "Bangalore",
            "url": "https://jobs.lever.co/swiggy/201",
            "description": "Extremely long description of React software engineer job with requirements and benefits...",
            "parse_confidence": 0.95,
            "source": "lever_api",
            "extraction_method": "lever_api"
        }

        deduped, _ = deduplicator.deduplicate([job_low, job_high])
        self.assertEqual(len(deduped), 1)
        # Should select job_high as primary due to higher confidence and description length
        self.assertEqual(deduped[0]["url"], "https://jobs.lever.co/swiggy/201")
        self.assertEqual(deduped[0]["extraction_method"], "lever_api")

    def test_08_background_worker_deduplication_integrity(self):
        """Verify BackgroundSearchWorker does not crash on deduplicator outputs."""
        worker = BackgroundSearchWorker()

        # Patch fetching
        mock_raw = {
            "career_pages": [],
            "indeed": [
                {
                    "title": "Software Engineer",
                    "company": "Swiggy",
                    "location": "Bangalore",
                    "url": "https://www.indeed.com/viewjob?jk=123",
                    "source": "indeed",
                    "description": "React developer"
                }
            ],
            "naukri": [],
            "linkedin": []
        }

        with patch.object(worker, "_fetch_all_sources_parallel", return_value=mock_raw):
            # Run internal cycle. Should complete without raising AttributeErrors
            res = worker._run_search_cycle_internal(custom_filters={
                "roles": ["Software Engineer"],
                "locations": ["Bangalore"],
                "is_manual_search": True
            })
            self.assertIsNotNone(res)
            self.assertEqual(res["total_jobs"], 1)

if __name__ == "__main__":
    unittest.main()
