import os
import json
from typing import List

BASE_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

class LocalEmbeddingService:
    _instance = None
    _model = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(LocalEmbeddingService, cls).__new__(cls)
        return cls._instance

    def __init__(self, model_name: str = "all-mpnet-base-v2"):
        self.model_name = model_name

    def _ensure_model_loaded(self):
        if LocalEmbeddingService._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                print(f"[LocalEmbeddingService] Loading sentence-transformer model '{self.model_name}' into memory...")
                LocalEmbeddingService._model = SentenceTransformer(self.model_name)
                print(f"[LocalEmbeddingService] Model '{self.model_name}' successfully loaded.")
            except Exception as e:
                raise RuntimeError(f"Failed to load sentence-transformers model '{self.model_name}': {e}")

    def get_embedding(self, text: str) -> List[float]:
        if not text:
            text = "empty"
        self._ensure_model_loaded()
        try:
            vec = LocalEmbeddingService._model.encode(text, convert_to_numpy=True)
            return vec.tolist()
        except Exception as e:
            raise RuntimeError(f"Error computing local embedding: {e}")

    def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        cleaned_texts = [t if t else "empty" for t in texts]
        self._ensure_model_loaded()
        try:
            vecs = LocalEmbeddingService._model.encode(cleaned_texts, convert_to_numpy=True)
            return vecs.tolist()
        except Exception as e:
            raise RuntimeError(f"Error computing batch local embeddings: {e}")
