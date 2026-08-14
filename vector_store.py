import os
import pickle
import numpy as np
import faiss
from typing import List, Dict, Any, Tuple

BASE_DIR = os.path.dirname(__file__)

class VectorStoreService:
    def __init__(self, index_path: str = os.path.join(BASE_DIR, "faiss_index.index"),
                 store_path: str = os.path.join(BASE_DIR, "faiss_index.store")):
        self.index_path = index_path
        self.store_path = store_path
        self.dimension = 768
        self.index = None
        self.doc_store = {} # maps idx -> metadata_dict
        self._load()

    def _load(self):
        if os.path.exists(self.index_path) and os.path.exists(self.store_path):
            try:
                self.index = faiss.read_index(self.index_path)
                with open(self.store_path, "rb") as f:
                    self.doc_store = pickle.load(f)
                return
            except Exception:
                pass
        self.index = faiss.IndexFlatL2(self.dimension)
        self.doc_store = {}

    def save(self):
        if self.index is not None:
            faiss.write_index(self.index, self.index_path)
            with open(self.store_path, "wb") as f:
                pickle.dump(self.doc_store, f)

    def add_embeddings(self, embeddings: List[List[float]], metadata: List[Dict[str, Any]]) -> None:
        if not embeddings:
            return

        vectors_np = np.array(embeddings, dtype=np.float32)
        start_id = self.index.ntotal

        self.index.add(vectors_np)

        for i, meta in enumerate(metadata):
            doc_id = start_id + i
            self.doc_store[doc_id] = meta

        self.save()

    def search(self, query_embedding: List[float], k: int = 5) -> List[Tuple[Dict[str, Any], float]]:
        if self.index is None or self.index.ntotal == 0:
            return []

        k = min(k, self.index.ntotal)
        query_np = np.array([query_embedding], dtype=np.float32)

        distances, indices = self.index.search(query_np, k)

        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx != -1 and idx in self.doc_store:
                results.append((self.doc_store[idx], float(dist)))
        return results

    def clear(self):
        self.index = faiss.IndexFlatL2(self.dimension)
        self.doc_store = {}
        if os.path.exists(self.index_path):
            os.remove(self.index_path)
        if os.path.exists(self.store_path):
            os.remove(self.store_path)
