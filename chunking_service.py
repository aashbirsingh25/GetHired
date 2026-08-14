import re
from typing import List, Dict, Any

class ChunkingService:
    def __init__(self, chunk_size: int = 600, overlap: int = 100):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def _split_into_units(self, text: str) -> List[str]:
        # 1. Split into paragraphs
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        units = []

        for p in paragraphs:
            if len(p) <= self.chunk_size:
                units.append(p)
            else:
                # 2. Split into sentences
                sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", p) if s.strip()]
                for s in sentences:
                    if len(s) <= self.chunk_size:
                        units.append(s)
                    else:
                        # 3. Split into words
                        words = s.split()
                        curr_words = []
                        curr_len = 0
                        for w in words:
                            if curr_len + len(w) + 1 > self.chunk_size:
                                units.append(" ".join(curr_words))
                                curr_words = [w]
                                curr_len = len(w)
                            else:
                                curr_words.append(w)
                                curr_len += len(w) + 1
                        if curr_words:
                            units.append(" ".join(curr_words))
        return units

    def chunk_text(self, text: str) -> List[Dict[str, Any]]:
        if not text or not text.strip():
            return []

        units = self._split_into_units(text)
        chunks = []
        curr_chunk = []
        curr_len = 0

        for unit in units:
            unit_len = len(unit)
            if curr_len + unit_len + (1 if curr_chunk else 0) > self.chunk_size:
                if curr_chunk:
                    chunk_content = " ".join(curr_chunk)
                    chunks.append(chunk_content)

                    # Handle overlap by keeping tail elements
                    overlap_units = []
                    overlap_len = 0
                    for u in reversed(curr_chunk):
                        if overlap_len + len(u) + 1 <= self.overlap:
                            overlap_units.insert(0, u)
                            overlap_len += len(u) + 1
                        else:
                            break

                    curr_chunk = overlap_units
                    curr_len = overlap_len

            curr_chunk.append(unit)
            curr_len += unit_len + 1

        if curr_chunk:
            chunks.append(" ".join(curr_chunk))

        result = []
        for i, content in enumerate(chunks):
            result.append({
                "chunk_id": f"chunk_{i}",
                "content": content
            })
        return result
