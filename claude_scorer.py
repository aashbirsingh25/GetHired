import json
import re
from typing import List, Dict, Any

def score_with_claude(resume_chunks: List[str], job_title: str, job_description: str, api_key: str, key_index: int, personalization_prompt: str = "") -> Dict[str, Any]:
    if not api_key or api_key.startswith("YOUR_"):
        raise ValueError("Invalid Claude API key")

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

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

    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text.strip()
    text = re.sub(r"^```json\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^```\s*", "", text, flags=re.MULTILINE)
    text = text.strip()

    data = json.loads(text)
    return {
        "score": int(data.get("score", 50)),
        "matched_skills": data.get("matched_skills", []),
        "reasoning": data.get("reasoning", "Matched via Claude"),
        "llm_used": "claude",
        "api_key_index": key_index,
        "notice": None
    }
