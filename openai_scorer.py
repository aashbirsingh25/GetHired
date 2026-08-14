import json
import re
from typing import List, Dict, Any

def score_with_openai(resume_chunks: List[str], job_title: str, job_description: str, api_key: str, key_index: int, personalization_prompt: str = "") -> Dict[str, Any]:
    if not api_key or api_key.startswith("YOUR_"):
        raise ValueError("Invalid OpenAI API key")

    import openai
    client = openai.OpenAI(api_key=api_key)

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

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )

    text = response.choices[0].message.content.strip()
    text = re.sub(r"^```json\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^```\s*", "", text, flags=re.MULTILINE)
    text = text.strip()

    data = json.loads(text)
    return {
        "score": int(data.get("score", 50)),
        "matched_skills": data.get("matched_skills", []),
        "reasoning": data.get("reasoning", "Matched via OpenAI"),
        "llm_used": "openai",
        "api_key_index": key_index,
        "notice": None
    }
