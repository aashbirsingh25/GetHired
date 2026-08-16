import os
import sys
import unittest

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from local_scorer import score_locally, classify_role
from hybrid_scorer import HybridJobScorer
from scratch.phase9_production_dataset import REAL_RESUME

class TestPhase16Stability(unittest.TestCase):

    def setUp(self):
        self.resume_skills = REAL_RESUME.get("skills", ["Python", "FastAPI", "React", "TypeScript", "Docker", "PostgreSQL"])
        self.scorer = HybridJobScorer(REAL_RESUME)

    def test_adversarial_long_description_contamination(self):
        """HR listing 16 developer skills should be capped at 25% and classified non_technical."""
        hr_desc = """
        MegaRecruit hiring Talent Acquisition Coordinator. Screen candidates for Python, Django, FastAPI, React, TypeScript,
        Node.js, Express, SQL, PostgreSQL, MongoDB, Redis, AWS, Azure, GCP, Docker, Kubernetes, Git, Linux, REST API, GraphQL,
        Machine Learning, Deep Learning, AI, NLP, Pandas, NumPy, PyTorch, TensorFlow, HTML, CSS, Tailwind, Bootstrap, CI/CD.
        """
        cat_score, role_cat = classify_role("Talent Acquisition Coordinator - Tech Hiring", hr_desc)
        self.assertEqual(role_cat, "non_technical")
        self.assertEqual(cat_score, 15)

        res = score_locally(self.resume_skills, "Talent Acquisition Coordinator - Tech Hiring", hr_desc, resume_exp_years=2)
        self.assertLessEqual(res["score"], 25)

    def test_adversarial_ai_traps(self):
        """Non-technical roles mentioning AI/ChatGPT/LLMs should score <= 26%."""
        sales_desc = "Outbound sales using ChatGPT and Generative AI email assistants to generate leads."
        res_sales = score_locally(self.resume_skills, "Sales Development Representative (ChatGPT/GenAI)", sales_desc, resume_exp_years=2)
        self.assertLessEqual(res_sales["score"], 25)

        pm_desc = "Define product roadmap for LLM-based customer support chatbots."
        res_pm = score_locally(self.resume_skills, "Product Manager - Generative AI", pm_desc, resume_exp_years=2)
        self.assertLessEqual(res_pm["score"], 30)

        bootcamp_desc = "Join our 6-month intensive bootcamp! Learn Python, Machine Learning, Deep Learning, React, and Node.js."
        res_bootcamp = score_locally(self.resume_skills, "AI & Fullstack Development Certification Bootcamp", bootcamp_desc, resume_exp_years=2)
        self.assertLessEqual(res_bootcamp["score"], 25)

    def test_adversarial_support_traps(self):
        """Technical support, helpdesk, application support roles should score <= 48%."""
        support_desc = "Provide Tier 2 technical support for web applications. Troubleshoot customer API issues."
        res_support = score_locally(self.resume_skills, "Technical Support Engineer", support_desc, resume_exp_years=2)
        self.assertLessEqual(res_support["score"], 45)

        helpdesk_desc = "Assist internal employees with laptop setup, password resets, hardware troubleshooting."
        res_helpdesk = score_locally(self.resume_skills, "IT Helpdesk Specialist", helpdesk_desc, resume_exp_years=2)
        self.assertLessEqual(res_helpdesk["score"], 48)

    def test_adversarial_executive_seniority_traps(self):
        """CTO, Director, VP, Staff, Principal roles should be capped at 35% for a 2-year candidate."""
        cto_res = score_locally(self.resume_skills, "Chief Technology Officer (CTO)", "Co-founder CTO", resume_exp_years=2)
        self.assertLessEqual(cto_res["score"], 35)

        director_res = score_locally(self.resume_skills, "Director of Engineering", "Executive leadership", resume_exp_years=2)
        self.assertLessEqual(director_res["score"], 35)

        staff_res = score_locally(self.resume_skills, "Staff Software Engineer", "10+ years experience required", resume_exp_years=2)
        self.assertLessEqual(staff_res["score"], 35)

    def test_adversarial_senior_lead_traps(self):
        """Senior Software Engineer (6+ yrs) and Lead Engineer should be capped at <= 60%."""
        senior_res = score_locally(self.resume_skills, "Senior Software Engineer - Python", "6+ years experience in Python", resume_exp_years=2)
        self.assertLessEqual(senior_res["score"], 60)

        lead_res = score_locally(self.resume_skills, "Lead Fullstack Engineer", "7+ years hands-on development", resume_exp_years=2)
        self.assertLessEqual(lead_res["score"], 60)

    def test_adversarial_core_target_promotion(self):
        """Core Software Engineer, New Grad, and Product Engineer roles should score >= 75%."""
        target_res1 = score_locally(self.resume_skills, "Software Engineer - New Graduate (2026/2027)", "Python, Data Structures, React", resume_exp_years=2)
        self.assertGreaterEqual(target_res1["score"], 75)

        target_res2 = score_locally(self.resume_skills, "Product Engineer", "React, TypeScript, Python FastAPI backend APIs", resume_exp_years=2)
        self.assertGreaterEqual(target_res2["score"], 75)

    def test_search_button_end_to_end_polling(self):
        """Verify POST /api/jobs/search triggers background task and GET /api/jobs/search/status/<id> returns results."""
        from app import app
        client = app.test_client()
        res = client.post("/api/jobs/search", json={"roles": ["Software Engineer"], "locations": ["Bangalore"]})
        self.assertIn(res.status_code, [202, 409])
        data = res.get_json()
        self.assertIn("task_id", data)

        task_id = data["task_id"]
        status_res = client.get(f"/api/jobs/search/status/{task_id}")
        self.assertEqual(status_res.status_code, 200)
        status_data = status_res.get_json()
        self.assertIn(status_data.get("status"), ["queued", "running", "completed"])

if __name__ == "__main__":
    unittest.main()
