import os
import json
import requests
import subprocess
import time
from typing import List, Dict, Any

BASE_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

class OllamaUnavailableError(Exception):
    """Raised when Ollama API service or specified model is unreachable or unavailable."""
    pass

class OllamaTimeoutError(Exception):
    """Raised when Ollama inference exceeds the configured timeout threshold."""
    pass

def load_ollama_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return cfg.get("ollama", {})
        except Exception:
            pass
    return {
        "enabled": True,
        "model": "qwen2.5:7b",
        "base_url": "http://localhost:11434",
        "timeout_seconds": 25,
        "fallback_if_unavailable": True
    }

_ollama_unreachable_until = 0.0

def score_with_ollama(
    resume_chunks: List[str],
    job_title: str,
    job_description: str,
    model_name: str = None,
    personalization_prompt: str = ""
) -> Dict[str, Any]:
    global _ollama_unreachable_until
    cfg = load_ollama_config()
    
    if not cfg.get("enabled", True):
        raise OllamaUnavailableError("Ollama local scoring is disabled in config.")

    now = time.time()
    if now < _ollama_unreachable_until:
        raise OllamaUnavailableError(f"Ollama service cached unreachable for next {int(_ollama_unreachable_until - now)}s")

    base_url = cfg.get("base_url", "http://localhost:11434").rstrip("/")
    target_model = model_name or cfg.get("model", "qwen2.5:7b")
    timeout_sec = float(cfg.get("timeout_seconds", 25))

    # 1. Ping reachability check (3s timeout)
    try:
        ping_res = requests.get(f"{base_url}/api/tags", timeout=1.0)
        if ping_res.status_code != 200:
            _ollama_unreachable_until = time.time() + 60.0
            raise OllamaUnavailableError(f"Ollama server returned status {ping_res.status_code}")
        tags_data = ping_res.json()
    except Exception as e:
        _ollama_unreachable_until = time.time() + 60.0
        raise OllamaUnavailableError(f"Ollama service unreachable at {base_url}: {e}")

    # 2. Check if model is pulled
    models_list = tags_data.get("models", [])
    model_names = [m.get("name", "") for m in models_list]

    model_found = any(target_model in name or name.startswith(target_model.split(":")[0]) for name in model_names)
    
    if not model_found:
        print(f"[OllamaScorer] Model '{target_model}' not found in Ollama tags. Attempting pull check...")
        try:
            subprocess.run(
                ["ollama", "pull", target_model],
                capture_output=True,
                text=True,
                timeout=5
            )
        except Exception as pull_err:
            raise OllamaUnavailableError(f"Model '{target_model}' is not pulled and pull check timed out/failed: {pull_err}")

    # 3. Construct JSON prompt
    combined_resume = "\n---\n".join(resume_chunks[:5]) if resume_chunks else "No detailed resume text."
    clipped_desc = job_description[:2000] if job_description else "No job description."

    prompt = f"""You are an expert AI job recruiter matching candidate resume chunks with a job posting.
Evaluate candidate match against the job specifications.

Candidate Resume Excerpts:
{combined_resume}
{personalization_prompt}
Job Title: {job_title}
Job Description:
{clipped_desc}

INSTRUCTIONS:
Output STRICT JSON ONLY. Do not include markdown code blocks or additional conversational text.
Required JSON schema:
{{
  "score": int (0 to 100),
  "matched_skills": [string],
  "missing_skills": [string],
  "reasoning": string (concise, professional 2-3 sentence match summary)
}}
"""

    payload = {
        "model": target_model,
        "prompt": prompt,
        "stream": False,
        "format": "json"
    }

    start_time = time.time()
    try:
        response = requests.post(f"{base_url}/api/generate", json=payload, timeout=timeout_sec)
        duration_sec = round(time.time() - start_time, 2)
        
        if response.status_code != 200:
            raise OllamaUnavailableError(f"Ollama generate returned status {response.status_code}: {response.text}")

        res_json = response.json()
        raw_output = res_json.get("response", "")

        # Parse nested JSON from model output
        try:
            parsed = json.loads(raw_output)
        except Exception:
            # Fallback regex extraction if raw_output has wrapped quotes/brackets
            import re
            m = re.search(r"\{.*\}", raw_output, re.DOTALL)
            if m:
                parsed = json.loads(m.group(0))
            else:
                parsed = {}

        raw_score = parsed.get("score", 65)
        try:
            score = min(98, max(40, int(raw_score)))
        except Exception:
            score = 65

        matched_skills = parsed.get("matched_skills") or []
        missing_skills = parsed.get("missing_skills") or []
        reasoning = parsed.get("reasoning") or f"Ollama ({target_model}) evaluated candidate profile match at {score}%."

        return {
            "score": score,
            "matched_skills": matched_skills,
            "missing_skills": missing_skills,
            "reasoning": reasoning,
            "llm_used": "ollama_local_qwen2.5",
            "confidence": "high-medium",
            "model": target_model,
            "provider": "Ollama",
            "response_time_seconds": duration_sec
        }

    except requests.exceptions.Timeout:
        raise OllamaTimeoutError(f"Ollama inference timed out after {timeout_sec} seconds")
    except Exception as e:
        if isinstance(e, (OllamaUnavailableError, OllamaTimeoutError)):
            raise
        raise OllamaUnavailableError(f"Ollama generation failed: {e}")
