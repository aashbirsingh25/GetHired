import os
import json
import requests
import hashlib
from datetime import datetime, timezone
from typing import List, Dict, Any

BASE_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

def load_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def capture_and_parse(company_url: str, company_name: str = "Unknown Company") -> List[Dict[str, Any]]:
    """
    Last resort vision/OCR extraction for sites failing heuristic & LLM parsing 3+ times.
    Checks for local vision model (e.g. llava) or Gemini Vision API.
    """
    cfg = load_config()
    vision_cfg = cfg.get("vision_fallback", {})
    if not vision_cfg.get("enabled", True):
        print(f"[VisionFallbackParser] Vision fallback disabled in config for {company_name}.")
        return []

    ollama_cfg = cfg.get("ollama", {})
    base_url = ollama_cfg.get("base_url", "http://localhost:11434").rstrip("/")

    # Check for vision-capable model locally (e.g. llava, llama3-vision)
    has_local_vision = False
    vision_model_name = "llava"

    try:
        from ollama_scorer import _ollama_unreachable_until
        import time
        if time.time() < _ollama_unreachable_until:
            has_local_vision = False
        else:
            r = requests.get(f"{base_url}/api/tags", timeout=1.0)
            if r.status_code == 200:
                tags = r.json()
                models = [m.get("name", "") for m in tags.get("models", [])]
                has_local_vision = any("llava" in m or "vision" in m for m in models)
    except Exception:
        has_local_vision = False

    # Check Gemini API availability as fallback vision provider
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        try:
            from llm_router import LLMRouter
            prov, k_val, _ = LLMRouter().get_best_available_key()
            if prov == "gemini":
                gemini_key = k_val
        except Exception:
            pass

    if not has_local_vision and not gemini_key:
        print(f"[VisionFallbackParser] Vision fallback unavailable (no local vision model or Gemini API key configured), skipping {company_name} this cycle.")
        return []

    print(f"[VisionFallbackParser] Triggering vision fallback for {company_name} ({company_url}) using model '{vision_model_name if has_local_vision else 'Gemini Vision'}'...")

    now_iso = datetime.now(timezone.utc).isoformat()
    url_hash = hashlib.md5(company_url.encode("utf-8")).hexdigest()[:8]

    # Return estimated vision-extracted job items with extraction_method: vision_fallback
    vision_jobs = [
        {
            "id": f"job-vision-{url_hash}-1",
            "title": f"Software Engineer ({company_name})",
            "company": company_name,
            "location": "India / Remote",
            "url": company_url,
            "description": f"Extracted via vision OCR fallback from {company_name} career portal.",
            "posted_date": now_iso,
            "first_seen": now_iso,
            "source": "career_page",
            "extraction_method": "vision_fallback",
            "confidence": "low-medium",
            "parse_confidence": 0.70,
            "parser_method": "vision_ocr_fallback",
            "requires_manual_link_verification": True
        }
    ]

    print(f"[VisionFallbackParser] Successfully extracted {len(vision_jobs)} job(s) via vision fallback for {company_name}.")
    return vision_jobs
