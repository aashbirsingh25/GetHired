import os
import sys
import unittest

PROJECT_ROOT = r"c:\Users\Aashbir\OneDrive\Desktop\GetHired"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from job_deduplicator import JobDeduplicator

class TestLiveIngestionDedupValidity(unittest.TestCase):

    def test_invalid_jobs_rejected_by_deduplicator(self):
        """Verify invalid non-job listings input to JobDeduplicator are rejected before output."""
        invalid_inputs = [
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
        
        deduplicator = JobDeduplicator()
        result_jobs, _ = deduplicator.deduplicate(invalid_inputs)
        
        self.assertEqual(len(result_jobs), 0, f"Expected 0 invalid jobs in output, but got {len(result_jobs)}: {[j['title'] for j in result_jobs]}")

    def test_legitimate_jobs_retained_by_deduplicator(self):
        """Verify legitimate jobs, interns, support, and promoter roles input to JobDeduplicator are retained."""
        legitimate_inputs = [
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
            },
            {
                "company": "TCS",
                "title": "Software Engineer",
                "url": "https://tcs.com/careers/job/11111",
                "location": "Mumbai",
                "posted_date": "2026-08-01"
            },
            {
                "company": "Paytm",
                "title": "Backend Engineer",
                "url": "https://paytm.com/careers/job/22222",
                "location": "Noida",
                "posted_date": "2026-08-01"
            }
        ]
        
        deduplicator = JobDeduplicator()
        result_jobs, _ = deduplicator.deduplicate(legitimate_inputs)
        
        self.assertEqual(len(result_jobs), 6, f"Expected 6 legitimate jobs retained, but got {len(result_jobs)}")
        retained_titles = [j["title"] for j in result_jobs]
        for item in legitimate_inputs:
            self.assertIn(item["title"], retained_titles, f"Legitimate job missing from output: {item['title']}")

if __name__ == "__main__":
    unittest.main()
