import os
import sys
import unittest

PROJECT_ROOT = r"c:\Users\Aashbir\OneDrive\Desktop\GetHired"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from local_scorer import classify_role

class TestRoleOverrideRegression(unittest.TestCase):

    def test_group_A_genuine_engineering(self):
        """A. Genuine engineering roles must classify as core_software, ai_ml, or adjacent_engineering."""
        cases = [
            ("AI Engineer", "Design and train deep learning ML models for NLP and inference.", 90, "ai_ml"),
            ("Backend Engineer", "Build scalable REST APIs and manage PostgreSQL databases.", 90, "core_software"),
            ("Software Engineer", "Full stack web software development using React and FastAPI.", 90, "core_software"),
            ("DevOps Engineer", "Automate CI/CD pipelines, Docker containerization, and AWS infrastructure deployment.", 75, "adjacent_engineering")
        ]
        for title, desc, exp_score, exp_cat in cases:
            score, cat = classify_role(title, desc)
            self.assertEqual(score, exp_score, f"Failed score for '{title}': got {score}, expected {exp_score}")
            self.assertEqual(cat, exp_cat, f"Failed category for '{title}': got {cat}, expected {exp_cat}")

    def test_group_B_genuine_support(self):
        """B. Genuine support roles must classify as support (score 45)."""
        cases = [
            ("Technical Support Engineer", "Provide technical support and helpdesk assistance to clients.", 45, "support"),
            ("Application Support Engineer", "Troubleshoot application issues and handle client tickets.", 45, "support"),
            ("Product Support Engineer", "Answer product support inquiries and guide user setup.", 45, "support")
        ]
        for title, desc, exp_score, exp_cat in cases:
            score, cat = classify_role(title, desc)
            self.assertEqual(score, exp_score, f"Failed score for '{title}': got {score}, expected {exp_score}")
            self.assertEqual(cat, exp_cat, f"Failed category for '{title}': got {cat}, expected {exp_cat}")

    def test_group_C_contradictory_title_body(self):
        """C. Contradictory engineering titles with support descriptions MUST downgrade to support (45)."""
        cases = [
            ("AI Engineer", "Provide customer support, helpdesk ticket management, and user assistance.", 45, "support"),
            ("Backend Engineer", "Provide technical support and ticket escalation for client accounts.", 45, "support"),
            ("Software Engineer", "Handle customer service inquiries, answer helpdesk calls, and resolve user issues.", 45, "support"),
            ("DevOps Engineer", "Manage IT helpdesk tickets and office desktop support.", 45, "support")
        ]
        for title, desc, exp_score, exp_cat in cases:
            score, cat = classify_role(title, desc)
            self.assertEqual(score, exp_score, f"Failed score for '{title}': got {score}, expected {exp_score}")
            self.assertEqual(cat, exp_cat, f"Failed category for '{title}': got {cat}, expected {exp_cat}")

    def test_group_D_weak_token_protection(self):
        """D. Weak support tokens alone MUST NOT downgrade genuine engineering roles."""
        cases = [
            ("Software Engineer", "Design backend services to support production systems.", 90, "core_software"),
            ("Software Engineer", "Develop internal tools to support internal users across engineering teams.", 90, "core_software"),
            ("Software Engineer", "Build a high-performance customer-facing API.", 90, "core_software"),
            ("Software Engineer", "Write automated scripts and CLI tooling to help developers.", 90, "core_software"),
            ("Backend Engineer", "On-call rotation to resolve incidents in microservice architecture.", 90, "core_software"),
            ("Software Engineer", "Investigate bugs and handle tickets related to engineering systems.", 90, "core_software")
        ]
        for title, desc, exp_score, exp_cat in cases:
            score, cat = classify_role(title, desc)
            self.assertEqual(score, exp_score, f"Failed score for '{title}': got {score}, expected {exp_score}")
            self.assertEqual(cat, exp_cat, f"Failed category for '{title}': got {cat}, expected {exp_cat}")

    def test_group_E_strong_phrases_individually(self):
        """E. Each strong support phrase in description MUST trigger downgrade for engineering title if no engineering context."""
        phrases = [
            "HELPDESK", "CUSTOMER SUPPORT", "TECHNICAL SUPPORT", "APPLICATION SUPPORT",
            "PRODUCT SUPPORT", "SERVICE DESK", "TICKET ESCALATION", "SUPPORT TICKETS"
        ]
        for phrase in phrases:
            title = "Software Engineer"
            desc = f"Primary duty involves {phrase} for client accounts."
            score, cat = classify_role(title, desc)
            self.assertEqual(score, 45, f"Phrase '{phrase}' failed to downgrade '{title}': got score {score}")
            self.assertEqual(cat, "support", f"Phrase '{phrase}' failed to downgrade '{title}': got category {cat}")

    def test_group_F_mixed_evidence(self):
        """F. Mixed evidence tests (engineering context protects role, multiple strong support without eng context downgrades)."""
        # Strong support phrase + strong engineering context -> engineering context protects!
        score1, cat1 = classify_role("Software Engineer", "Provide technical support to clients while engineering customer-facing API endpoints.")
        self.assertEqual(score1, 90)
        self.assertEqual(cat1, "core_software")

        # Multiple strong support phrases + no engineering context -> downgrades to support!
        score2, cat2 = classify_role("AI Engineer", "Handle helpdesk, ticket escalation, and support tickets for users.")
        self.assertEqual(score2, 45)
        self.assertEqual(cat2, "support")

        # Weak support tokens + strong engineering context -> stays engineering!
        score3, cat3 = classify_role("Software Engineer", "Help developers resolve incidents and support production systems.")
        self.assertEqual(score3, 90)
        self.assertEqual(cat3, "core_software")

if __name__ == "__main__":
    unittest.main()
