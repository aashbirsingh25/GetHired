import json
import os
import time
from background_search_worker import BackgroundSearchWorker
from hybrid_scorer import HybridJobScorer

def main():
    print("=== TESTING REAL LLM SCORING ROUTING CYCLE ===")
    BASE_DIR = os.path.dirname(__file__)
    
    # Load resume
    with open(os.path.join(BASE_DIR, "resume_store.json"), "r", encoding="utf-8") as f:
        resume_dict = json.load(f)

    # Load jobs
    with open(os.path.join(BASE_DIR, "jobs_store.json"), "r", encoding="utf-8") as f:
        jobs_data = json.load(f)

    jobs = jobs_data.get("jobs", [])[:15]
    print(f"Loaded {len(jobs)} jobs for scoring test.")

    # Record log length before
    log_file = os.path.join(BASE_DIR, "scoring_log.json")
    if os.path.exists(log_file):
        with open(log_file, "r", encoding="utf-8") as f:
            prev_logs = json.load(f).get("logs", [])
    else:
        prev_logs = []

    prev_count = len(prev_logs)

    scorer = HybridJobScorer(resume_dict)

    start_t = time.time()
    for idx, job in enumerate(jobs):
        # Force re-score by removing existing match cache
        job_copy = dict(job)
        job_copy.pop("match", None)
        res = scorer.score_job(job_copy)
        print(f"[{idx+1}/15] Job: {job_copy.get('company')} - {job_copy.get('title')[:30]} | LLM: {res.get('llm_used')} | Score: {res.get('score')} | Tier: {res.get('tier')}")

    elapsed = round(time.time() - start_t, 2)

    # Read logs after
    if os.path.exists(log_file):
        with open(log_file, "r", encoding="utf-8") as f:
            new_logs = json.load(f).get("logs", [])
    else:
        new_logs = []

    cycle_events = new_logs[prev_count:]
    print(f"\n=== SCORING CYCLE COMPLETED IN {elapsed}s ===")
    print(f"Total new scoring events recorded: {len(cycle_events)}")

    tier_breakdown = {}
    llm_breakdown = {}

    for ev in cycle_events:
        llm = ev.get("llm_used", "unknown")
        tier = ev.get("tier", "unknown")
        tier_breakdown[tier] = tier_breakdown.get(tier, 0) + 1
        llm_breakdown[llm] = llm_breakdown.get(llm, 0) + 1

    print("\n--- TIER BREAKDOWN ---")
    for t, c in sorted(tier_breakdown.items(), key=lambda x: str(x[0])):
        print(f"  Tier {t}: {c} job(s)")

    print("\n--- LLM / SCORER BREAKDOWN ---")
    for llm, c in llm_breakdown.items():
        print(f"  {llm}: {c} job(s)")

if __name__ == "__main__":
    main()
