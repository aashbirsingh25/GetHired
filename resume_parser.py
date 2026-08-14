import re
import os
from typing import Dict, Any, List

def parse_resume(file_path: str) -> Dict[str, Any]:
    ext = os.path.splitext(file_path)[1].lower()
    raw_text = ""

    if ext == ".pdf":
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            pages_text = []
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    pages_text.append(t)
            raw_text = "\n".join(pages_text)
    elif ext in [".docx", ".doc"]:
        import docx
        doc = docx.Document(file_path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        raw_text = "\n".join(paragraphs)
    else:
        # Plain text fallback
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            raw_text = f.read()

    # Extract skills
    known_skills = [
        "Python", "Java", "C++", "C#", "JavaScript", "TypeScript", "React", "Angular",
        "Vue", "Node.js", "Express", "Flask", "Django", "FastAPI", "Spring Boot",
        "SQL", "PostgreSQL", "MySQL", "MongoDB", "Redis", "AWS", "Azure", "GCP",
        "Docker", "Kubernetes", "Git", "Linux", "REST API", "GraphQL", "Microservices",
        "Machine Learning", "Deep Learning", "NLP", "Pandas", "NumPy", "PyTorch", "TensorFlow",
        "HTML", "CSS", "Tailwind", "Bootstrap", "Agile", "Scrum", "CI/CD"
    ]

    found_skills = []
    text_upper = raw_text.upper()
    for skill in known_skills:
        # Match word boundary
        pattern = r"\b" + re.escape(skill.upper()) + r"\b"
        if re.search(pattern, text_upper):
            found_skills.append(skill)

    # Estimate experience
    exp_pattern = r"(\d+)\+?\s*(?:years?|yrs?)\s*(?:of\s*)?(?:experience|exp)?"
    matches = re.findall(exp_pattern, raw_text, re.IGNORECASE)
    estimated_exp = None
    if matches:
        exps = [int(m) for m in matches if int(m) <= 40]
        if exps:
            estimated_exp = max(exps)

    return {
        "raw_text": raw_text,
        "skills": sorted(list(set(found_skills))),
        "estimated_years_experience": estimated_exp
    }

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        res = parse_resume(sys.argv[1])
        print(f"Skills found ({len(res['skills'])}): {res['skills']}")
        print(f"Est Exp: {res['estimated_years_experience']}")
