import os
import sys
import unittest

PROJECT_ROOT = r"c:\Users\Aashbir\OneDrive\Desktop\GetHired"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from store_integrity_checker import check_job_posting_validity

class TestValidityFilterRegression(unittest.TestCase):

    def test_known_bad_records_rejected(self):
        """1. Known bad records (courses, blog articles, summits) MUST be rejected."""
        bad_records = [
            {
                "company": "upGrad",
                "title": "Executive Post Graduate Certificate in AI-Native Software Engineering - IIT Kharagpur",
                "url": "https://www.upgrad.com/executive-post-graduate-in-ai-native-software-engineering/",
                "location": "Online"
            },
            {
                "company": "Airbnb India",
                "title": "74% of Gen Z want small town trips over big cities—three creatives lead the way",
                "url": "https://news.airbnb.com/have-you-considered/",
                "location": "India"
            },
            {
                "company": "Everstage",
                "title": "Northstar '26 - The invite-only Revenue Architect summit by Everstage. NYC, Aug 20.",
                "url": "https://www.everstage.com/events/northstar26",
                "location": "New York"
            }
        ]
        for rec in bad_records:
            is_valid, reasons = check_job_posting_validity(rec)
            self.assertFalse(is_valid, f"Failed to reject non-job: {rec['title']} (reasons: {reasons})")

    def test_known_legitimate_jobs_retained(self):
        """2. Legitimate jobs, internships, traineeships, and support roles MUST be retained (false-positive check)."""
        good_records = [
            {
                "company": "Unacademy",
                "title": "Intern (Product Development)",
                "url": "https://www.unacademy.com/careers/job-angellist-106-4300",
                "location": "Bangalore",
                "posted_date": "2026-08-01"
            },
            {
                "company": "Vyapar",
                "title": "Sales Promoter - Surat",
                "url": "https://vyaparapp.in/careers/jobdetails/135180",
                "location": "Surat",
                "posted_date": "2026-08-01"
            },
            {
                "company": "Microsoft India",
                "title": "Technical Support Engineer",
                "url": "https://careers.microsoft.com/us/en/job/123456",
                "location": "Hyderabad",
                "posted_date": "2026-08-01"
            },
            {
                "company": "Goldman Sachs",
                "title": "Software Engineering Trainee",
                "url": "https://goldmansachs.com/careers/job/98765",
                "location": "Bengaluru",
                "posted_date": "2026-08-01"
            }
        ]
        for rec in good_records:
            is_valid, reasons = check_job_posting_validity(rec)
            self.assertTrue(is_valid, f"Accidentally rejected legitimate job: {rec['title']} (reasons: {reasons})")

    def test_title_patterns(self):
        """3. Specific non-job title patterns MUST trigger rejection."""
        patterns = [
            "Executive Post Graduate Certificate in Data Science",
            "Certification Program in Cloud Computing",
            "Bootcamp in Full Stack Development",
            "Diploma in AI",
            "Annual Technology Summit 2026",
            "Webinar on Software Testing",
            "Hackathon 2026",
            "Join our Talent Community",
            "Expression of Interest - Software Engineer"
        ]
        for t in patterns:
            job = {"company": "Test Corp", "title": t, "url": "https://testcorp.com/careers/p=123", "location": "Remote", "posted_date": "2026-08-01"}
            is_valid, _ = check_job_posting_validity(job)
            self.assertFalse(is_valid, f"Title pattern failed to trigger rejection: '{t}'")

    def test_url_path_patterns(self):
        """4. Specific non-job URL paths MUST trigger rejection."""
        paths = ["/blog/article-1", "/news/press-release", "/press/announcement", "/events/summit-2026", "/courses/python-101"]
        for p in paths:
            job = {"company": "Test Corp", "title": "Software Engineer", "url": f"https://testcorp.com{p}", "location": "Remote", "posted_date": "2026-08-01"}
            is_valid, _ = check_job_posting_validity(job)
            self.assertFalse(is_valid, f"URL path pattern failed to trigger rejection: '{p}'")

if __name__ == "__main__":
    unittest.main()
