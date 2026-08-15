import os
import json
import threading
import time
from typing import Dict, Any, List

BASE_DIR = os.path.dirname(__file__)
LOG_FILE = os.path.join(BASE_DIR, "consensus_log.json")

def load_json(filepath, default):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

import time

def save_json(filepath, data, indent=2):
    dir_name = os.path.dirname(filepath) or "."
    os.makedirs(dir_name, exist_ok=True)
    tmp_path = f"{filepath}.tmp_{os.getpid()}_{threading.get_ident()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp_path, filepath)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02)
    except Exception as e:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        raise e

def extract_numeric_score(val: Any, default: int = 50) -> int:
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return max(0, min(100, int(val)))
    if isinstance(val, dict):
        s_val = val.get("score")
        return extract_numeric_score(s_val, default=default)
    if isinstance(val, str):
        try:
            return max(0, min(100, int(float(val.strip()))))
        except (ValueError, TypeError):
            return default
    return default

def should_verify(score: int) -> bool:
    """Returns True if score is in the ambiguous band (40-60 inclusive)."""
    score_val = extract_numeric_score(score, default=50)
    return 40 <= score_val <= 60

_consensus_logs_cache = None

def verify_with_second_opinion(
    resume_chunks: List[str],
    resume_skills: List[str],
    job_title: str,
    job_description: str,
    primary_score: Any,
    primary_source: str,
    primary_tier: int
) -> Dict[str, Any]:
    global _consensus_logs_cache
    p_score = extract_numeric_score(primary_score, default=50)
    if not should_verify(p_score):
        return {"consensus": False, "reason": "Score outside ambiguous 40-60 band"}

    second_score = p_score
    secondary_source = "none"

    try:
        if primary_tier in [1, 2, 3]:
            # Get second opinion from Ollama (Tier 4)
            from ollama_scorer import score_with_ollama
            res = score_with_ollama(resume_chunks, job_title, job_description)
            second_score = extract_numeric_score(res, default=p_score)
            secondary_source = "ollama_local_qwen2.5"

        elif primary_tier == 4:
            # Get second opinion from Tier 5 (Hybrid Semantic)
            from hybrid_semantic_fallback import score_with_hybrid_semantic
            res = score_with_hybrid_semantic(resume_skills, job_title, job_description)
            second_score = extract_numeric_score(res, default=p_score)
            secondary_source = "hybrid_semantic_fallback"

        else:
            # For Tier 5 or Tier 6, second opinion from local keyword scorer
            from local_scorer import score_locally
            res = score_locally(resume_skills, job_title, job_description)
            second_score = extract_numeric_score(res, default=p_score)
            secondary_source = "local_scorer"

    except Exception as e:
        print(f"[ScoreConsensusChecker] Second opinion verification skipped/failed: {e}")
        return {"consensus": False, "reason": str(e)}

    diff = abs(p_score - second_score)
    disagrees = diff > 20
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    consensus_info = {
        "consensus": not disagrees,
        "primary_score": p_score,
        "secondary_score": second_score,
        "secondary_source": secondary_source,
        "score_diff": diff,
        "timestamp": now_iso
    }

    if disagrees:
        consensus_info["flag"] = "scores_disagree"
        consensus_info["note"] = "Primary and verification model disagree significantly - review manually"
    else:
        consensus_info["confidence_boost"] = "verified by second model"

    # Log to in-memory consensus logs cache
    if _consensus_logs_cache is None:
        _consensus_logs_cache = {"verifications": []}
    verifications = _consensus_logs_cache.setdefault("verifications", [])
    verifications.append(consensus_info)
    _consensus_logs_cache["verifications"] = verifications[-100:]

    print(f"[ScoreConsensusChecker] Verified score {primary_score} vs {second_score} ({secondary_source}). Disagree: {disagrees}")
    return consensus_info

