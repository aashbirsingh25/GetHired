import numpy as np
from typing import Dict, Any, List
from embedding_service import EmbeddingService
from vector_store import VectorStoreService
from local_scorer import score_locally

def score_with_hybrid_semantic(
    resume_skills: List[str],
    job_title: str,
    job_description: str,
    vector_store: VectorStoreService = None,
    embedding_service: EmbeddingService = None,
    resume_raw_text: str = "",
    resume_exp_years: int = None
) -> Dict[str, Any]:
    embedder = embedding_service or EmbeddingService()
    vs = vector_store or VectorStoreService()

    # Calculate local multi-component scores (skill, role, experience)
    local_res = score_locally(
        resume_skills=resume_skills,
        job_title=job_title,
        job_description=job_description,
        resume_raw_text=resume_raw_text,
        resume_exp_years=resume_exp_years
    )

    skill_score = local_res.get("skill_score")
    skill_confidence = local_res.get("skill_confidence", "explicit")
    role_score = local_res.get("role_score", 50)
    exp_score = local_res.get("experience_score", 50)
    matched_skills = local_res.get("matched_skills", [])
    missing_skills = local_res.get("missing_skills", [])

    # 1. Try real embedding semantic similarity
    job_text = (job_title + "\n" + job_description).strip()
    job_emb = embedder.get_embedding(job_text)

    semantic_score = None
    has_real_semantic = False

    if job_emb is not None:
        top_chunks = vs.search(job_emb, k=5)
        if top_chunks:
            distances = [dist for _, dist in top_chunks]
            min_dist = min(distances)
            # For normalized 768-d vectors, FAISS L2 distance relates to cosine sim by: cos_sim = (2 - L2_dist) / 2
            cosine_sim = max(0.0, min(1.0, (2.0 - min_dist) / 2.0))
            semantic_score = int(round(cosine_sim * 100))
            has_real_semantic = True

    # 2. Combine components dynamically
    if has_real_semantic:
        if skill_score is not None:
            # Standard formula: 35% skill + 30% semantic + 20% role + 15% experience
            combined = int(round(
                0.35 * skill_score +
                0.30 * semantic_score +
                0.20 * role_score +
                0.15 * exp_score
            ))
        else:
            # UNKNOWN skills: re-normalize remaining weights (30% sem + 20% role + 15% exp = 65% total)
            combined = int(round(
                (0.30 * semantic_score + 0.20 * role_score + 0.15 * exp_score) / 0.65
            ))
        final_score = min(98, max(10, combined))
        
        # Apply UNKNOWN skill score cap (65%)
        if skill_confidence == "unknown":
            final_score = min(65, final_score)

        # Apply Hard Seniority Cap (60%) when candidate experience <= 2 years
        cand_exp = resume_exp_years if resume_exp_years is not None else 0
        is_senior_job = local_res.get("is_senior_job", False)
        if cand_exp <= 2 and is_senior_job:
            final_score = min(60, final_score)

        score_method = "hybrid_semantic"
        reasoning = (
            f"Hybrid match score: {final_score}% (Semantic: {semantic_score}%, "
            f"Skills: {skill_score if skill_score is not None else 'N/A'} [{skill_confidence}], "
            f"Role: {role_score}%, Experience: {exp_score}%). "
            f"Matched {len(matched_skills)} key skill(s)."
        )
    else:
        # Honest fallback when embeddings are unavailable / store empty
        final_score = local_res.get("score", 50)
        score_method = "keyword_fallback"
        reasoning = (
            f"Multi-component local score: {final_score}% (Skills: {skill_score if skill_score is not None else 'N/A'} [{skill_confidence}], "
            f"Role: {role_score}%, Experience: {exp_score}%). "
            f"Matched {len(matched_skills)} key skill(s)."
        )

    return {
        "score": final_score,
        "semantic_score": semantic_score if has_real_semantic else None,
        "skill_score": skill_score,
        "skill_confidence": skill_confidence,
        "role_score": role_score,
        "role_category": local_res.get("role_category", "unknown"),
        "experience_score": exp_score,
        "confidence": "high" if has_real_semantic else "medium",
        "matched_skills": matched_skills,
        "missing_skills": missing_skills,
        "reasoning": reasoning,
        "llm_used": score_method,
        "api_key_index": None,
        "notice": "Semantic similarity engine active" if has_real_semantic else "Local multi-component fallback active"
    }

