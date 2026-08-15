import re
from typing import List, Dict, Any, Tuple

KNOWN_TECH_SKILLS = [
    "Python", "Java", "C++", "C#", "Go", "C", "R", "JavaScript", "TypeScript", "React", "Angular",
    "Vue", "Node.js", "Express", "Flask", "Django", "FastAPI", "Spring Boot",
    "SQL", "PostgreSQL", "MySQL", "MongoDB", "Redis", "AWS", "Azure", "GCP",
    "Docker", "Kubernetes", "Git", "Linux", "REST API", "GraphQL", "Microservices",
    "Machine Learning", "Deep Learning", "AI", "NLP", "Pandas", "NumPy", "PyTorch", "TensorFlow",
    "HTML", "CSS", "Tailwind", "Bootstrap", "CI/CD"
]

CUSTOM_SKILL_PATTERNS = {
    "Go": re.compile(
        r"(?:^|\s|\b)(?:GOLANG|GO\s*(?:DEVELOPER|ENGINEER|PROGRAMMING|LANGUAGE|BACKEND|STACK|CODE)|(?:WRITTEN IN|EXPERIENCE IN|PROFICIENT IN|KNOWLEDGE OF|USING)\s+GO|GO\s*[/,]\s*(?:PYTHON|C\+\+|JAVA|RUST|DOCKER|KUBERNETES))(?:$|\s|\b)",
        re.IGNORECASE
    ),
    "C": re.compile(
        r"(?:^|\s|\b)(?:C\s*(?:/\s*C\+\+|PROGRAMMING|PROGRAMMER|LANGUAGE|DEVELOPER|ENGINEER|CODE|DRIVER)|EMBEDDED\s+C|C\s+(?:OR|,)\s+C\+\+|(?:WRITTEN IN|EXPERIENCE IN|PROFICIENT IN|KNOWLEDGE OF)\s+C)(?:$|\s|\b)",
        re.IGNORECASE
    ),
    "R": re.compile(
        r"(?:^|\s|\b)(?:RSTUDIO|R-STUDIO|R\s*(?:PROGRAMMING|LANGUAGE|STUDIO|DEVELOPER|ANALYTICS)|(?:WRITTEN IN|EXPERIENCE IN|PROFICIENT IN)\s+R|R\s*[/,]\s*(?:PYTHON|SQL|SAS|STATA|PANDAS))(?:$|\s|\b)",
        re.IGNORECASE
    ),
    "AI": re.compile(
        r"(?:^|\s|\b)(?:ARTIFICIAL INTELLIGENCE|GENAI|GENERATIVE AI|AI/ML|AI\s*(?:ENGINEER|DEVELOPER|MODEL|SYSTEM|PLATFORM|SOLUTION|APPLICATION|RESEARCH|TOOLS))(?:$|\s|\b)",
        re.IGNORECASE
    ),
    "CSS": re.compile(
        r"(?:^|\s|\b)(?:CSS3?|HTML\s*[/,&\+]\s*CSS|TAILWIND|BOOTSTRAP|CSS\s+DEVELOPER|CSS\s+STYLING)(?:$|\s|\b)",
        re.IGNORECASE
    )
}

SKILL_PATTERNS = []
for skill in KNOWN_TECH_SKILLS:
    if skill in CUSTOM_SKILL_PATTERNS:
        SKILL_PATTERNS.append((skill, CUSTOM_SKILL_PATTERNS[skill]))
    else:
        SKILL_PATTERNS.append((skill, re.compile(r"(?:^|\s|\b)" + re.escape(skill.upper()) + r"(?:$|\s|\b)")))

def classify_role(title: str, description: str) -> Tuple[int, str]:
    title_upper = (title or "").upper()
    desc_upper = (description or "").upper()
    
    # 0. Non-Job Listing / Course / Bootcamp Rejection
    course_phrases = [
        "BOOTCAMP", "CERTIFICATION PROGRAM", "POST GRADUATE CERTIFICATE",
        "MASTERCLASS", "WEBINAR", "HACKATHON", "SUMMIT"
    ]
    if any(cp in title_upper for cp in course_phrases):
        return 15, "non_technical"

    # 1. High-Priority Support Roles (overrides generic "Engineer")
    support_phrases = [
        "TECHNICAL SUPPORT", "APPLICATION SUPPORT", "PRODUCT SUPPORT", "CUSTOMER SUPPORT",
        "SUPPORT ENGINEER", "SUPPORT EXECUTIVE", "SUPPORT SPECIALIST", "SUPPORT LEAD",
        "ESCALATION MANAGER", "CUSTOMER SERVICE", "SERVICE DESK", "DESK SUPPORT",
        "PRODUCTION SUPPORT", "IT SUPPORT"
    ]
    if any(sp in title_upper for sp in support_phrases):
        return 45, "support"

    # Helper function to check for strong support description overrides
    def check_description_support_override(current_score: int, current_category: str) -> Tuple[int, str]:
        if not desc_upper:
            return current_score, current_category
            
        strong_support_phrases = [
            "HELPDESK", "CUSTOMER SUPPORT", "TECHNICAL SUPPORT", "APPLICATION SUPPORT",
            "PRODUCT SUPPORT", "SERVICE DESK", "TICKET ESCALATION", "SUPPORT TICKETS",
            "TIER 1 SUPPORT", "TIER 2 SUPPORT", "DESK SUPPORT"
        ]
        eng_context_phrases = [
            "SUPPORT PRODUCTION SYSTEMS", "SUPPORT INTERNAL USERS", "CUSTOMER-FACING API",
            "HELP DEVELOPERS", "RESOLVE INCIDENTS", "ENGINEERING SYSTEMS", "PRODUCTION WORKLOADS"
        ]
        
        has_strong_support = any(re.search(r"\b" + re.escape(sp) + r"\b", desc_upper) for sp in strong_support_phrases)
        has_eng_context = any(re.search(r"\b" + re.escape(ep) + r"\b", desc_upper) for ep in eng_context_phrases)
        
        if has_strong_support and not has_eng_context:
            return 45, "support"
        return current_score, current_category

    if any(bl in title_upper.lower() for bl in ["developer docs", "developer documentation", "api documentation", "api reference", "documentation"]):
        return 15, "non_technical"

    # 2. Non-Technical Roles (Recruiting, Marketing, Sales, Legal, Finance)
    non_tech_phrases = [
        "MARKETING", "SALES", "RECRUITER", "RECRUITING", "HR", "HUMAN RESOURCES",
        "OPERATIONS", "CONTRACTS", "PROGRAM MANAGER", "PROJECT MANAGER", "ACCOUNT MANAGER",
        "ACCOUNT EXECUTIVE", "BUSINESS DEVELOPMENT", "LEGAL", "FINANCE", "ACCOUNTANT", "CONTENT"
    ]
    is_non_tech = any(re.search(r"\b" + re.escape(nt) + r"\b", title_upper) for nt in non_tech_phrases)
    is_dev_role = any(re.search(r"\b" + re.escape(dev) + r"\b", title_upper) for dev in ["SOFTWARE ENGINEER", "SOFTWARE DEVELOPER", "PYTHON DEVELOPER", "FULLSTACK", "BACKEND DEVELOPER", "FRONTEND DEVELOPER"])
    if is_non_tech and not is_dev_role:
        return 15, "non_technical"

    # 2b. Non-Software Engineering Roles (Mechanical, Civil, Chemical, Electrical, NOC, Hardware)
    non_sw_eng_phrases = [
        "MECHANICAL", "CIVIL", "CHEMICAL", "ELECTRICAL", "HARDWARE", "BIOMEDICAL",
        "AEROSPACE", "ENVIRONMENTAL", "STRUCTURAL", "NOC ENGINEER", "NETWORK OPERATIONS CENTER"
    ]
    if any(nse in title_upper for nse in non_sw_eng_phrases) and not any(t in title_upper for t in ["SOFTWARE", "DEVELOPER", "EMBEDDED SOFTWARE"]):
        return 20, "non_software_engineering"

    # 3. AI / ML
    ai_ml_phrases = [
        "AI ENGINEER", "AI APPLICATIONS ENGINEER", "AI APPLICATION ENGINEER", "AI DEVELOPER",
        "MACHINE LEARNING ENGINEER", "ML ENGINEER", "DATA SCIENTIST",
        "DEEP LEARNING ENGINEER", "NLP ENGINEER", "ARTIFICIAL INTELLIGENCE", "AI RESEARCH"
    ]
    if any(am in title_upper for am in ai_ml_phrases):
        return check_description_support_override(90, "ai_ml")

    # 4. Core Software Engineering
    core_sw_phrases = [
        "SOFTWARE ENGINEER", "SOFTWARE DEVELOPER", "BACKEND ENGINEER", "BACKEND DEVELOPER",
        "FULL STACK ENGINEER", "FULL STACK DEVELOPER", "FULLSTACK ENGINEER", "FULLSTACK DEVELOPER",
        "FULL STACK", "FULLSTACK", "FRONTEND ENGINEER", "FRONTEND DEVELOPER", "WEB DEVELOPER", "API DEVELOPER",
        "PLATFORM ENGINEER", "SOFTWARE ARCHITECT", "APPLICATION DEVELOPER", "APPLICATION ENGINEER", "APPLICATIONS ENGINEER",
        "PRODUCT ENGINEER", "SYSTEMS DEVELOPER", "PYTHON DEVELOPER", "REACT DEVELOPER", "NODE DEVELOPER"
    ]
    if any(cs in title_upper for cs in core_sw_phrases) or title_upper.strip() in ["DEVELOPER", "ENGINEER", "SOFTWARE ENGINEER"]:
        score = 95 if any(kw in title_upper for kw in ["INTERN", "TRAINEE", "FRESHER"]) else 90
        return check_description_support_override(score, "core_software")

    # 5. Adjacent Engineering & QA
    qa_phrases = ["QA AUTOMATION", "AUTOMATION ENGINEER", "QUALITY ASSURANCE", "QA ENGINEER", "TEST ENGINEER"]
    if any(qa in title_upper for qa in qa_phrases):
        return check_description_support_override(45, "testing_qa")

    adjacent_phrases = [
        "DEVOPS ENGINEER", "CLOUD ENGINEER", "INFRASTRUCTURE ENGINEER", "SRE",
        "SITE RELIABILITY", "DATABASE ENGINEER", "DATABASE RELIABILITY", "DBA",
        "SYSTEMS ENGINEER", "SECURITY OPERATIONS", "SECURITY ENGINEER", "DATA ENGINEER",
        "NETWORK ENGINEER"
    ]
    if any(adj in title_upper for adj in adjacent_phrases):
        return check_description_support_override(70, "adjacent_engineering")

    # 6. Fallback General Tech
    if any(tr in title_upper for tr in ["SOFTWARE", "DEVELOPER", "ENGINEER", "PRODUCT DEVELOPMENT", "DATA", "TECH"]):
        return check_description_support_override(60, "general_tech")

    return 40, "unknown"


SYNONYM_MAP = {
    "POSTGRES": "PostgreSQL",
    "POSTGRESQL": "PostgreSQL",
    "JS": "JavaScript",
    "JAVASCRIPT": "JavaScript",
    "TS": "TypeScript",
    "TYPESCRIPT": "TypeScript",
    "AMAZON WEB SERVICES": "AWS",
    "AWS": "AWS",
    "GOOGLE CLOUD PLATFORM": "GCP",
    "GCP": "GCP",
    "MICROSOFT AZURE": "Azure",
    "AZURE": "Azure",
    "NODE": "Node.js",
    "NODEJS": "Node.js",
    "NODE.JS": "Node.js",
    "REACT": "React",
    "REACTJS": "React",
    "REACT.JS": "React",
    "RESTFUL API": "REST API",
    "RESTFUL APIS": "REST API",
    "REST API": "REST API",
    "REST APIS": "REST API"
}

def canonicalize_skill(skill_str: str) -> str:
    if not skill_str:
        return ""
    clean = skill_str.strip().upper()
    return SYNONYM_MAP.get(clean, skill_str.strip())

def score_locally(
    resume_skills: List[str],
    job_title: str,
    job_description: str,
    resume_raw_text: str = "",
    resume_exp_years: int = None
) -> Dict[str, Any]:
    combined_text = (job_title + " " + job_description).upper()
    title_upper = (job_title or "").upper()
    desc_len = len((job_description or "").strip())

    # 1. Skill Overlap Component with Synonym Resolution
    raw_job_skills = []
    for skill, pat in SKILL_PATTERNS:
        if pat.search(combined_text):
            raw_job_skills.append(skill)

    # Check for text synonyms not caught by exact skill regex
    if re.search(r"\bPOSTGRES\b", combined_text) and "PostgreSQL" not in raw_job_skills:
        raw_job_skills.append("PostgreSQL")
    if re.search(r"\b(?:AMAZON WEB SERVICES|AWS EC2)\b", combined_text) and "AWS" not in raw_job_skills:
        raw_job_skills.append("AWS")
    if re.search(r"\b(?:GOOGLE CLOUD PLATFORM|GCP)\b", combined_text) and "GCP" not in raw_job_skills:
        raw_job_skills.append("GCP")
    if re.search(r"\b(?:JS|JAVASCRIPT)\b", combined_text) and "JavaScript" not in raw_job_skills:
        raw_job_skills.append("JavaScript")
    if re.search(r"\b(?:TS|TYPESCRIPT)\b", combined_text) and "TypeScript" not in raw_job_skills:
        raw_job_skills.append("TypeScript")

    canonical_job_skills = sorted(list(set([canonicalize_skill(s) for s in raw_job_skills])))

    canonical_resume_skills = set([canonicalize_skill(s) for s in (resume_skills or [])])

    matched = []
    for js in canonical_job_skills:
        if js in canonical_resume_skills or js.upper() in [s.upper() for s in canonical_resume_skills]:
            matched.append(js)

    matched = sorted(list(set(matched)))
    missing = [s for s in canonical_job_skills if s not in matched]

    if len(canonical_job_skills) >= 2 or (len(canonical_job_skills) == 1 and desc_len <= 300):
        skill_confidence = "explicit"
        ratio = len(matched) / float(len(canonical_job_skills))
        skill_score = min(98, max(10, int(round(ratio * 100))))
    elif len(canonical_job_skills) == 1 and desc_len > 300:
        skill_confidence = "inferred"
        skill_score = 65 if len(matched) == 1 else 10
    else:
        skill_confidence = "unknown"
        skill_score = None

    # 2. Role / Title Relevance Component
    role_score, role_category = classify_role(job_title, job_description)

    # 3. Experience & Seniority Compatibility Component
    cand_exp = resume_exp_years if resume_exp_years is not None else 0

    norm_senior_text = re.sub(r"[^A-Z0-9\+]", " ", combined_text)

    exec_senior_patterns = [
        r"\bPRINCIPAL\b", r"\bSTAFF\b", r"\bDIRECTOR\b", r"\bPARTNER\b", r"\bFELLOW\b",
        r"\bVP\b", r"\bVICE\s+PRESIDENT\b", r"\bCTO\b", r"\bCHIEF\s+(?:TECHNOLOGY|TECHNICAL|ARCHITECT|OFFICER)\b",
        r"\b10\+\s*YEARS\b", r"\b12\+\s*YEARS\b", r"\b15\+\s*YEARS\b"
    ]
    is_exec_senior_job = any(re.search(pat, norm_senior_text) for pat in exec_senior_patterns)

    senior_patterns = [
        r"\bSENIOR\b", r"\bLEAD\b", r"\bARCHITECT\b", r"\bMANAGER\b",
        r"\b5\+\s*YEARS\b", r"\b8\+\s*YEARS\b"
    ]
    is_senior_job = is_exec_senior_job or any(re.search(pat, norm_senior_text) for pat in senior_patterns)

    entry_patterns = [r"\bINTERN\b", r"\bFRESHER\b", r"\bENTRY\s*LEVEL\b", r"\bGRADUATE\b", r"\b0-1\s*YEAR\b", r"\b0-2\s*YEARS\b"]
    is_entry_job = any(re.search(pat, combined_text) for pat in entry_patterns)

    if is_exec_senior_job:
        exp_score = 15 if cand_exp <= 2 else 85
    elif is_senior_job:
        exp_score = 30 if cand_exp <= 2 else 85
    elif is_entry_job or "INTERN" in title_upper:
        exp_score = 95 if cand_exp <= 2 else 70
    else:
        exp_score = 75 if cand_exp <= 3 else 90

    # 4. Multi-component Weighted Score Combination
    if skill_score is not None:
        combined_score = int(round(0.45 * skill_score + 0.35 * role_score + 0.20 * exp_score))
    else:
        combined_score = int(round((0.35 * role_score + 0.20 * exp_score) / 0.55))

    final_score = min(98, max(10, combined_score))

    # Apply UNKNOWN skill score cap (65%)
    if skill_confidence == "unknown":
        final_score = min(65, final_score)

    # Apply Hard Seniority Caps when candidate experience <= 2 years
    if cand_exp <= 2:
        if is_exec_senior_job:
            final_score = min(35, final_score)
        elif is_senior_job:
            final_score = min(60, final_score)

    # Apply Hard Role Caps for non-software / non-technical / support roles
    if role_category in ("non_technical", "non_software_engineering"):
        final_score = min(25, final_score)
    elif role_category in ("support", "testing_qa"):
        final_score = min(45, final_score)

    return {
        "score": final_score,
        "skill_score": skill_score,
        "skill_confidence": skill_confidence,
        "role_score": role_score,
        "role_category": role_category,
        "experience_score": exp_score,
        "is_senior_job": is_senior_job,
        "is_exec_senior_job": is_exec_senior_job,
        "confidence": "medium",
        "matched_skills": matched,
        "missing_skills": missing,
        "reasoning": f"Local score: skill={skill_score} ({skill_confidence}), role={role_score} ({role_category}), exp={exp_score}%.",
        "llm_used": "local_fallback",
        "api_key_index": None,
        "notice": "Local multi-component scoring engine active"
    }

