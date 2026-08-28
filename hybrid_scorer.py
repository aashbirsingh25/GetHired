import json
import os
import time
from datetime import datetime
from typing import Dict, Any, List

from embedding_service import EmbeddingService
from vector_store import VectorStoreService
from llm_router import LLMRouter
from gemini_scorer import score_with_gemini
from groq_scorer import score_with_groq
from claude_scorer import score_with_claude
from openai_scorer import score_with_openai
from ollama_scorer import score_with_ollama, OllamaUnavailableError, OllamaTimeoutError
from hybrid_semantic_fallback import score_with_hybrid_semantic
from local_scorer import score_locally
from score_consensus_checker import should_verify, verify_with_second_opinion
from feedback_example_selector import FeedbackExampleSelector

from relevance_predictor import RelevancePredictor
from cycle_yield_tracker import CycleYieldTracker

LOG_FILE = os.path.join(os.path.dirname(__file__), "scoring_log.json")
_scoring_logs_cache = None

def log_scoring_event(event: Dict[str, Any]):
    global _scoring_logs_cache
    if _scoring_logs_cache is None:
        _scoring_logs_cache = {"logs": []}

    _scoring_logs_cache.setdefault("logs", []).append(event)
    if len(_scoring_logs_cache["logs"]) > 500:
        _scoring_logs_cache["logs"] = _scoring_logs_cache["logs"][-500:]


class HybridJobScorer:
    def __init__(self, resume_dict: Dict[str, Any], vector_store: VectorStoreService = None, llm_router: LLMRouter = None):
        self.resume = resume_dict or {}
        self.embedding_service = EmbeddingService()
        self.vector_store = vector_store or VectorStoreService()
        self.llm_router = llm_router or LLMRouter()
        self.example_selector = FeedbackExampleSelector()
        self.relevance_predictor = RelevancePredictor()
        self.cycle_yield_tracker = CycleYieldTracker()
        self.resume_hash = self.resume.get("version_hash", "default")
        self.resume_skills = self.resume.get("skills", [])
        self.resume_raw_text = self.resume.get("raw_text", "")
        self.resume_exp_years = self.resume.get("estimated_years_experience")


    def _attach_consensus_if_needed(self, result: Dict[str, Any], resume_chunks: List[str], job_title: str, job_desc: str) -> Dict[str, Any]:
        score = result.get("score", 50)
        tier = result.get("tier", 1)
        llm_used = result.get("llm_used", "unknown")

        if should_verify(score):
            try:
                consensus_data = verify_with_second_opinion(
                    resume_chunks=resume_chunks,
                    resume_skills=self.resume_skills,
                    job_title=job_title,
                    job_description=job_desc,
                    primary_score=score,
                    primary_source=llm_used,
                    primary_tier=tier
                )
                result["consensus_verification"] = consensus_data
                if consensus_data.get("consensus") and consensus_data.get("confidence_boost"):
                    result["confidence"] = f"{result.get('confidence', 'medium')} ({consensus_data['confidence_boost']})"
                elif consensus_data.get("flag") == "scores_disagree":
                    result["confidence"] = "disputed (model disagreement)"
            except Exception as e:
                print(f"[HybridJobScorer] Consensus verification error: {e}")

        return result

    def _determine_routing_target(self, company: str, title: str, desc: str) -> Dict[str, Any]:
        rel_score = self.relevance_predictor.predict_relevance(company, title)
        
        sem_res = score_with_hybrid_semantic(
            self.resume_skills, title, desc, self.vector_store, self.embedding_service,
            resume_raw_text=self.resume_raw_text, resume_exp_years=self.resume_exp_years
        )
        sem_score = sem_res.get("score", 50)
        
        if 40 <= sem_score <= 75:
            ambiguity_band = "ambiguous"
        else:
            ambiguity_band = "clear"

        current_hour = datetime.now().hour
        yield_mult = self.cycle_yield_tracker.get_yield_multiplier(current_hour)
        headroom_info = self.llm_router.get_quota_headroom_info(current_hour)
        quota_low = headroom_info.get("is_low", False)

        is_high_relevance = rel_score >= 0.60
        is_ambiguous = ambiguity_band == "ambiguous"

        if is_high_relevance and is_ambiguous:
            if yield_mult < 0.8 and quota_low:
                target_tier = "ollama"
                reason = f"Routed to Ollama - high relevance ({rel_score:.2f}), ambiguous match, but downgraded due to low-yield cycle ({yield_mult}x) and tight LLM quota headroom"
            elif quota_low and rel_score < 0.75:
                target_tier = "ollama"
                reason = f"Routed to Ollama - high relevance ({rel_score:.2f}), ambiguous match, but downgraded due to tight LLM quota headroom"
            else:
                target_tier = "paid_llm"
                reason = f"Routed to paid tier (Gemini/Groq/Claude) - high relevance ({rel_score:.2f}), ambiguous match, standard/high-yield cycle ({yield_mult}x)"
        elif is_high_relevance and not is_ambiguous:
            target_tier = "ollama"
            reason = f"Routed to Ollama - high relevance ({rel_score:.2f}), clear match from first pass, saving paid tier budget"
        elif not is_high_relevance and is_ambiguous:
            target_tier = "ollama"
            reason = f"Routed to Ollama - low relevance ({rel_score:.2f}), ambiguous match, moderate effort tier"
        else:
            target_tier = "hybrid_semantic"
            reason = f"Routed to hybrid_semantic directly - low relevance ({rel_score:.2f}), clear match from first pass, skipping LLM"

        return {
            "target_tier": target_tier,
            "relevance_score": rel_score,
            "ambiguity_band": ambiguity_band,
            "cycle_yield_multiplier": yield_mult,
            "routing_reason": reason,
            "semantic_first_pass": sem_res
        }


    def score_job(self, job: Dict[str, Any], force_tier: str = None) -> Dict[str, Any]:
        job_id = job.get("id", "unknown")
        job_title = job.get("title", "")
        job_desc = job.get("description", "")
        company = job.get("company", "")

        # Check existing match cache. A forced paid-tier request bypasses a
        # cached cheap-tier result (that is the whole point of refinement),
        # but reuses a cached paid-tier result.
        existing_match = job.get("match")
        if existing_match and existing_match.get("resume_version_hash") == self.resume_hash:
            if not force_tier or existing_match.get("tier") in (1, 2):
                return existing_match

        # 1. Determine routing target based on Phase 11 matrix
        routing = self._determine_routing_target(company, job_title, job_desc)
        target_tier = routing["target_tier"]
        if force_tier:
            target_tier = force_tier
            routing["target_tier"] = force_tier
            routing["reason"] = f"Forced to {force_tier} (quality refinement pass)"

        # Embed job description & query top 5 FAISS resume chunks
        job_emb = self.embedding_service.get_embedding(job_title + "\n" + job_desc)
        top_chunks_meta = self.vector_store.search(job_emb, k=5)
        resume_chunks = [meta.get("content", "") for meta, _ in top_chunks_meta]

        if not resume_chunks and self.resume.get("raw_text"):
            resume_chunks = [self.resume.get("raw_text")[:2000]]

        pers_prompt, pers_count = self.example_selector.build_prompt_injection(job)

        now_iso = datetime.now().isoformat()
        start_time = time.time()

        # Helper to attach Phase 11 metadata to result
        def _enrich_result(res, tier_selected_name):
            res["relevance_score"] = routing["relevance_score"]
            res["ambiguity_band"] = routing["ambiguity_band"]
            res["cycle_yield_multiplier"] = routing["cycle_yield_multiplier"]
            res["routing_reason"] = routing["routing_reason"]

            from local_scorer import classify_role
            _, r_cat = classify_role(job_title, job_desc)
            if r_cat in ("non_technical", "non_software_engineering"):
                res["score"] = min(25, res.get("score", 50))
            elif r_cat in ("support", "testing_qa"):
                res["score"] = min(45, res.get("score", 50))

            return res

        # Direct Hybrid Semantic Fallback route (skip LLM)
        if target_tier == "hybrid_semantic":
            semantic_result = routing["semantic_first_pass"]
            duration = round(time.time() - start_time, 2)
            semantic_result["scored_at"] = now_iso
            semantic_result["resume_version_hash"] = self.resume_hash
            semantic_result["tier"] = 5
            semantic_result = _enrich_result(semantic_result, "hybrid_semantic")
            semantic_result = self._attach_consensus_if_needed(semantic_result, resume_chunks, job_title, job_desc)

            log_scoring_event({
                "timestamp": now_iso,
                "job_id": job_id,
                "tier": 5,
                "llm_used": "hybrid_semantic_fallback",
                "key_index": None,
                "status": "tier5_direct_routing",
                "score": semantic_result.get("score"),
                "response_time_seconds": duration,
                "relevance_score": routing["relevance_score"],
                "ambiguity_band": routing["ambiguity_band"],
                "cycle_yield_multiplier": routing["cycle_yield_multiplier"],
                "tier_selected": "hybrid_semantic",
                "routing_reason": routing["routing_reason"],
                "consensus": semantic_result.get("consensus_verification")
            })
            return semantic_result

        # Tiers 1-3: LLM provider API rotation (Gemini / Groq [Tier 1] -> Claude [Tier 2] -> OpenAI [Tier 3, Inactive: no free tier as of 2026, code preserved for reactivation if paid key added])
        if target_tier == "paid_llm":
            tried_keys = set()
            tier_map = {"gemini": 1, "groq": 1, "claude": 2, "openai": 3}
            # Why the LLM tier gave up, so a fallback to the local scorer is
            # never silent. A silent fallback hid exhausted Gemini quotas for
            # hours: the refinement pass looked like it was running while every
            # score actually came from local_scorer.
            llm_failures = []

            while True:
                provider, api_key, key_idx = self.llm_router.get_best_available_key()

                if (provider, key_idx) in tried_keys or provider is None:
                    if provider is None and not llm_failures:
                        llm_failures.append("router returned no usable key "
                                            "(all keys out of daily quota or cooling down)")
                    break

                tried_keys.add((provider, key_idx))
                tier_num = tier_map.get(provider, 1)

                try:
                    if provider == "gemini":
                        result = score_with_gemini(resume_chunks, job_title, job_desc, api_key, key_idx, personalization_prompt=pers_prompt)
                    elif provider == "groq":
                        result = score_with_groq(resume_chunks, job_title, job_desc, api_key, key_idx, personalization_prompt=pers_prompt)
                    elif provider == "claude":
                        result = score_with_claude(resume_chunks, job_title, job_desc, api_key, key_idx, personalization_prompt=pers_prompt)
                    elif provider == "openai":
                        result = score_with_openai(resume_chunks, job_title, job_desc, api_key, key_idx, personalization_prompt=pers_prompt)
                    else:
                        break

                    duration = round(time.time() - start_time, 2)
                    self.llm_router.mark_used(provider, key_idx)
                    result["scored_at"] = now_iso
                    result["resume_version_hash"] = self.resume_hash
                    result["tier"] = tier_num
                    result["personalization_examples_used"] = pers_count
                    result = _enrich_result(result, provider)
                    result = self._attach_consensus_if_needed(result, resume_chunks, job_title, job_desc)

                    log_scoring_event({
                        "timestamp": now_iso,
                        "job_id": job_id,
                        "tier": tier_num,
                        "llm_used": provider,
                        "key_index": key_idx,
                        "status": "success",
                        "score": result.get("score"),
                        "response_time_seconds": duration,
                        "personalization_examples_used": pers_count,
                        "relevance_score": routing["relevance_score"],
                        "ambiguity_band": routing["ambiguity_band"],
                        "cycle_yield_multiplier": routing["cycle_yield_multiplier"],
                        "tier_selected": provider,
                        "routing_reason": routing["routing_reason"],
                        "consensus": result.get("consensus_verification")
                    })
                    return result

                except Exception as e:
                    err_text = str(e).lower()
                    llm_failures.append(f"{provider}[{key_idx}]: {str(e)[:70]}")
                    # Dead key (invalid/revoked): remove from rotation.
                    # Transient (429 rate limit, quota message, 5xx, timeout):
                    # cool the key down and move to the next one.
                    if "api key not valid" in err_text or "api_key_invalid" in err_text \
                            or "401" in err_text or "permission" in err_text or "403" in err_text:
                        self.llm_router.on_quota_error(provider, key_idx)
                    elif "429" in err_text or "quota" in err_text or "rate" in err_text \
                            or "exhaust" in err_text or "resource" in err_text:
                        self.llm_router.on_rate_limit(provider, key_idx, cooldown_seconds=300)
                    else:
                        self.llm_router.on_rate_limit(provider, key_idx, cooldown_seconds=60)
                    log_scoring_event({
                        "timestamp": now_iso,
                        "job_id": job_id,
                        "tier": tier_num,
                        "llm_used": provider,
                        "key_index": key_idx,
                        "error": str(e),
                        "status": "fallback"
                    })

        # Reaching here means the LLM tier produced nothing. Say so out loud,
        # once per job, with the actual reason - otherwise a refinement pass
        # silently degrades to local scoring and looks healthy from outside.
        if target_tier == "paid_llm":
            why = "; ".join(llm_failures[:3]) if llm_failures else "no attempt made"
            print(f"[HybridScorer] LLM tier unavailable for '{job_title[:40]}' "
                  f"-> degrading to cheaper tier. Reason: {why}")

        # Tier 4: Ollama Local Model
        try:
            ollama_start = time.time()
            ollama_result = score_with_ollama(resume_chunks, job_title, job_desc, personalization_prompt=pers_prompt)
            duration = round(time.time() - ollama_start, 2)
            
            ollama_result["scored_at"] = now_iso
            ollama_result["resume_version_hash"] = self.resume_hash
            ollama_result["tier"] = 4
            ollama_result["personalization_examples_used"] = pers_count
            ollama_result = _enrich_result(ollama_result, "ollama")
            ollama_result = self._attach_consensus_if_needed(ollama_result, resume_chunks, job_title, job_desc)

            log_scoring_event({
                "timestamp": now_iso,
                "job_id": job_id,
                "tier": 4,
                "llm_used": "ollama_local_qwen2.5",
                "key_index": None,
                "status": "tier4_ollama_success",
                "score": ollama_result.get("score"),
                "response_time_seconds": duration,
                "personalization_examples_used": pers_count,
                "relevance_score": routing["relevance_score"],
                "ambiguity_band": routing["ambiguity_band"],
                "cycle_yield_multiplier": routing["cycle_yield_multiplier"],
                "tier_selected": "ollama",
                "routing_reason": routing["routing_reason"],
                "consensus": ollama_result.get("consensus_verification")
            })
            return ollama_result

        except (OllamaUnavailableError, OllamaTimeoutError, Exception) as ollama_err:
            log_scoring_event({
                "timestamp": now_iso,
                "job_id": job_id,
                "tier": 4,
                "llm_used": "ollama_local_qwen2.5",
                "error": str(ollama_err),
                "status": "tier4_ollama_failed_fallthrough_to_tier5"
            })

        # Tier 5: Hybrid Semantic Fallback
        try:
            semantic_start = time.time()
            semantic_result = score_with_hybrid_semantic(
                self.resume_skills, job_title, job_desc, self.vector_store, self.embedding_service,
                resume_raw_text=self.resume_raw_text, resume_exp_years=self.resume_exp_years
            )
            duration = round(time.time() - semantic_start, 2)

            semantic_result["scored_at"] = now_iso
            semantic_result["resume_version_hash"] = self.resume_hash
            semantic_result["tier"] = 5
            semantic_result = _enrich_result(semantic_result, "hybrid_semantic")
            semantic_result = self._attach_consensus_if_needed(semantic_result, resume_chunks, job_title, job_desc)

            log_scoring_event({
                "timestamp": now_iso,
                "job_id": job_id,
                "tier": 5,
                "llm_used": "hybrid_semantic_fallback",
                "key_index": None,
                "status": "tier5_semantic_fallback",
                "score": semantic_result.get("score"),
                "response_time_seconds": duration,
                "relevance_score": routing["relevance_score"],
                "ambiguity_band": routing["ambiguity_band"],
                "cycle_yield_multiplier": routing["cycle_yield_multiplier"],
                "tier_selected": "hybrid_semantic",
                "routing_reason": routing["routing_reason"],
                "consensus": semantic_result.get("consensus_verification")
            })
            return semantic_result

        except Exception as semantic_err:
            log_scoring_event({
                "timestamp": now_iso,
                "job_id": job_id,
                "tier": 5,
                "llm_used": "hybrid_semantic_fallback",
                "error": str(semantic_err),
                "status": "fallback_to_tier6"
            })

        # Tier 6: Pure local keyword fallback
        local_start = time.time()
        local_result = score_locally(
            self.resume_skills, job_title, job_desc,
            resume_raw_text=self.resume_raw_text, resume_exp_years=self.resume_exp_years
        )
        duration = round(time.time() - local_start, 2)

        local_result["scored_at"] = now_iso
        local_result["resume_version_hash"] = self.resume_hash
        local_result["tier"] = 6
        local_result = _enrich_result(local_result, "local_fallback")

        log_scoring_event({
            "timestamp": now_iso,
            "job_id": job_id,
            "tier": 6,
            "llm_used": "local_fallback",
            "key_index": None,
            "status": "tier6_local_fallback",
            "score": local_result.get("score"),
            "response_time_seconds": duration,
            "relevance_score": routing["relevance_score"],
            "ambiguity_band": routing["ambiguity_band"],
            "cycle_yield_multiplier": routing["cycle_yield_multiplier"],
            "tier_selected": "local_fallback",
            "routing_reason": routing["routing_reason"]
        })
        return local_result

