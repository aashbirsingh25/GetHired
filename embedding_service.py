import hashlib
import random
import os
from typing import List, Dict

BASE_DIR = os.path.dirname(__file__)

def load_dotenv(dotenv_path=None):
    if dotenv_path is None:
        dotenv_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(dotenv_path):
        try:
            with open(dotenv_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'").strip('"')
                    if k and k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass

class EmbeddingService:
    _local_failed_warned = False
    _local_available = None
    _cache: Dict[str, List[float]] = {}

    def __init__(self, model_name: str = "all-mpnet-base-v2"):
        load_dotenv()
        self.model_name = model_name

    def is_available(self) -> bool:
        """Check if a real embedding engine (local sentence-transformers or Gemini API) is available."""
        if EmbeddingService._local_available is True:
            return True
        if EmbeddingService._local_available is None:
            try:
                import sentence_transformers
                EmbeddingService._local_available = True
                return True
            except Exception:
                EmbeddingService._local_available = False
        key = os.environ.get("GEMINI_API_KEY") or (os.environ.get("GEMINI_API_KEYS", "").split(",")[0] if os.environ.get("GEMINI_API_KEYS") else None)
        if key and not key.startswith("YOUR_"):
            return True
        return False

    def get_embedding(self, text: str, api_key: str = None) -> List[float]:
        if not text:
            text = "empty"

        if text in EmbeddingService._cache:
            return EmbeddingService._cache[text]

        # 1. Primary: Try local sentence-transformers model if available
        if EmbeddingService._local_available is not False:
            try:
                from local_embedding_service import LocalEmbeddingService
                local_service = LocalEmbeddingService(model_name=self.model_name)
                emb = local_service.get_embedding(text)
                if len(emb) == 768:
                    EmbeddingService._local_available = True
                    EmbeddingService._cache[text] = emb
                    return emb
            except Exception as local_err:
                EmbeddingService._local_available = False
                if not EmbeddingService._local_failed_warned:
                    print(f"[EmbeddingService] Local embedding notice ({local_err}). Local sentence-transformers unavailable.")
                    EmbeddingService._local_failed_warned = True

        # 2. Fallback: Try Gemini API embedding if key configured
        key = api_key or os.environ.get("GEMINI_API_KEY") or (os.environ.get("GEMINI_API_KEYS", "").split(",")[0] if os.environ.get("GEMINI_API_KEYS") else None)
        if key and not key.startswith("YOUR_"):
            try:
                import google.generativeai as genai
                genai.configure(api_key=key)
                res = genai.embed_content(
                    model="models/text-embedding-004",
                    content=text
                )
                emb = res.get("embedding", [])
                if isinstance(emb, dict) and "values" in emb:
                    emb = emb["values"]
                if len(emb) == 768:
                    EmbeddingService._cache[text] = emb
                    return emb
            except Exception:
                pass

        # Real embeddings are unavailable — return None (honest fallback)
        return None

    def get_embeddings(self, texts: List[str], api_key: str = None) -> List[List[float]]:
        embs = [self.get_embedding(t, api_key=api_key) for t in texts]
        return [e for e in embs if e is not None]

