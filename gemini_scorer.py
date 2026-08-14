import json
import re
from typing import List, Dict, Any

def score_with_gemini(resume_chunks: List[str], job_title: str, job_description: str, api_key: str, key_index: int, personalization_prompt: str = "") -> Dict[str, Any]:
    if not api_key or api_key.startswith("YOUR_"):
        raise ValueError("Invalid Gemini API key")

    import google.generativeai as genai
    genai.configure(api_key=api_key)

    try:
        model = genai.GenerativeModel("gemini-3.5-flash")
    except Exception:
        model = genai.GenerativeModel("gemini-flash-latest")

    excerpts = "\n---\n".join(resume_chunks[:5]) if resume_chunks else "No resume text provided."
    prompt = f"""
JOB TITLE: {job_title}
JOB DESCRIPTION: {job_description[:2000]}

RELEVANT RESUME EXCERPTS:
{excerpts}
{personalization_prompt}
Score this match 0-100 as strict JSON only (no markdown formatting, no text before or after):
{{"score": <int 0-100>, "matched_skills": [<strings>], "reasoning": "<one sentence>"}}
"""

    response = model.generate_content(prompt)
    text = response.text.strip()
    
    # Strip json markdown blocks if present
    text = re.sub(r"^```json\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^```\s*", "", text, flags=re.MULTILINE)
    text = text.strip()

    data = json.loads(text)
    return {
        "score": int(data.get("score", 50)),
        "matched_skills": data.get("matched_skills", []),
        "reasoning": data.get("reasoning", "Matched via Gemini"),
        "llm_used": "gemini",
        "api_key_index": key_index,
        "notice": None
    }
