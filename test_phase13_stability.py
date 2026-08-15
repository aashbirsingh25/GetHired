import unittest
import os
import json

from local_scorer import classify_role, score_locally
from hybrid_scorer import HybridJobScorer
from priority_sorter import PrioritySorter
from scratch.phase13_unseen_dataset import get_unseen_dataset, UNSEEN_RESUME

class TestPhase13Stability(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import tempfile
        import shutil
        from chunking_service import ChunkingService
        from embedding_service import EmbeddingService
        from vector_store import VectorStoreService

        cls.temp_dir = tempfile.mkdtemp()
        index_path = os.path.join(cls.temp_dir, "temp_vs.index")
        store_path = os.path.join(cls.temp_dir, "temp_vs.store")

        cls.vs = VectorStoreService(index_path=index_path, store_path=store_path)
        cls.vs.clear()

        chunker = ChunkingService(chunk_size=600, overlap=100)
        chunks = chunker.chunk_text(UNSEEN_RESUME["raw_text"])
        embedder = EmbeddingService()

        embeddings = []
        metadata_list = []
        for c in chunks:
            emb = embedder.get_embedding(c["content"])
            if emb is not None:
                embeddings.append(emb)
                metadata_list.append({
                    "chunk_id": c["chunk_id"],
                    "content": c["content"],
                    "version_hash": UNSEEN_RESUME["version_hash"]
                })
        if embeddings:
            cls.vs.add_embeddings(embeddings, metadata_list)

        cls.unseen_jobs = get_unseen_dataset()
        cls.scorer = HybridJobScorer(UNSEEN_RESUME, vector_store=cls.vs)
        cls.sorter = PrioritySorter(global_filters={})

        scored = []
        for j in cls.unseen_jobs:
            j_copy = dict(j)
            j_copy["match"] = cls.scorer.score_job(j_copy)
            scored.append(j_copy)

        cls.ranked_unseen = cls.sorter.sort_jobs(scored, global_filters={})

    @classmethod
    def tearDownClass(cls):
        try:
            import shutil
            shutil.rmtree(cls.temp_dir)
        except Exception:
            pass

    def test_01_course_and_bootcamp_rejection(self):
        """Verify bootcamp and course titles are classified as non_technical (score 15)."""
        score, cat = classify_role("Full Stack Web Development Bootcamp", "6-month intensive coding bootcamp")
        self.assertEqual(cat, "non_technical")
        self.assertEqual(score, 15)

    def test_02_vp_and_cto_executive_seniority_cap(self):
        """Verify VP, CTO, Vice President titles trigger executive seniority cap (35%) for junior candidate."""
        res_vp = score_locally(UNSEEN_RESUME["skills"], "VP of Engineering - Cloud Security", "Leading 200+ engineers", resume_exp_years=2)
        res_cto = score_locally(UNSEEN_RESUME["skills"], "Field CTO - Enterprise Strategy", "Evangelizing solution to CTOs", resume_exp_years=2)
        self.assertLessEqual(res_vp["score"], 35)
        self.assertLessEqual(res_cto["score"], 35)

    def test_03_unseen_precision_at_5_is_100_percent(self):
        """Verify Precision@5 on unseen 105-job dataset is 100%."""
        top_5 = self.ranked_unseen[:5]
        p5 = sum(1 for j in top_5 if j["human_label"] in ("A", "B")) / 5.0
        self.assertEqual(p5, 1.0)

    def test_04_unseen_precision_at_10_is_100_percent(self):
        """Verify Precision@10 on unseen 105-job dataset is 100%."""
        top_10 = self.ranked_unseen[:10]
        p10 = sum(1 for j in top_10 if j["human_label"] in ("A", "B")) / 10.0
        self.assertEqual(p10, 1.0)

    def test_05_unseen_precision_at_20_is_at_least_95_percent(self):
        """Verify Precision@20 on unseen dataset is >= 95%."""
        top_20 = self.ranked_unseen[:20]
        p20 = sum(1 for j in top_20 if j["human_label"] in ("A", "B")) / 20.0
        self.assertGreaterEqual(p20, 0.95)

    def test_06_unseen_tier_e_false_positive_rate_is_zero(self):
        """Verify 0% of Tier E non-tech/course jobs in unseen dataset score >= 50."""
        tier_e = [j for j in self.ranked_unseen if j["human_label"] == "E"]
        fp_e = [j for j in tier_e if j["match"].get("score", 0) >= 50]
        self.assertEqual(len(fp_e), 0)

    def test_07_unseen_mrr_is_1_point_0(self):
        """Verify MRR on unseen dataset is 1.0 (Top ranked job is Tier A)."""
        self.assertEqual(self.ranked_unseen[0]["human_label"], "A")

    def test_08_java_vs_javascript_no_false_overlap(self):
        """Verify 'Java' does not extract as 'JavaScript' or vice versa."""
        res_java = score_locally(["Java"], "Java Software Engineer", "Core Java Spring Boot")
        res_js = score_locally(["JavaScript"], "Java Software Engineer", "Core Java Spring Boot")
        self.assertNotIn("JavaScript", res_java["matched_skills"])
        self.assertNotIn("JavaScript", res_js["matched_skills"])

    def test_09_c_vs_cpp_no_false_overlap(self):
        """Verify 'C' does not falsely match 'C++' when only C++ is required."""
        res = score_locally(["C"], "C++ Software Engineer", "C++ algorithms and STL")
        self.assertNotIn("C", res["matched_skills"])

    def test_10_pyspark_vs_python_data_engineering(self):
        """Verify Python developer matching PySpark data engineering role."""
        res = score_locally(UNSEEN_RESUME["skills"], "Data Engineer - PySpark", "PySpark and Databricks ETL")
        self.assertGreaterEqual(res["score"], 40)

if __name__ == "__main__":
    unittest.main()
