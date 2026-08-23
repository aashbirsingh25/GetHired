import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_FILE = os.path.join(BASE_DIR, "jobs_store.json")
METRICS_FILE = os.path.join(BASE_DIR, "company_metrics.json")
COMPANIES_FILE = os.path.join(BASE_DIR, "companies.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

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

from company_ranker import rank_companies
from pattern_store import PatternStore
from browser_scanner import BrowserScanner, compute_parse_confidence_score
from company_classifier import update_company_difficulty
from apify_scanner import ApifyScanner, ApifyUnavailableError
from threshold_optimizer import get_scan_confidence_threshold, record_outcome
from scan_scheduler import partition_companies, update_yield_streak, next_cycle_number

# ATS types whose extraction is pure HTTP (requests) in
# BrowserScanner.scan_company — they never launch the browser, so they are
# safe to scan from worker threads in parallel.
_API_ATS_TYPES = ("workday", "greenhouse", "lever", "ashby", "smartrecruiters", "keka", "turbohire")
_API_URL_HINTS = (
    "myworkdayjobs.com", "greenhouse.io", "lever.co", "ashbyhq.com",
    "smartrecruiters.com", ".keka.com", "turbohire.co",
)

def _is_api_scannable(company: dict) -> bool:
    """Mirror of BrowserScanner.scan_company's dispatch: True when the scan
    will use a direct ATS REST API (no Playwright browser involved)."""
    url = (company.get("career_url") or "").lower()
    ats_type = (company.get("ats") or "").lower()
    return ats_type in _API_ATS_TYPES or any(h in url for h in _API_URL_HINTS)

class ScanCoordinator:
    def __init__(self):
        self.pattern_store = PatternStore()
        self.scanner = BrowserScanner(headless=True)

    def run_scan(self, target_company_id=None):
        """Runs a scan cycle across companies in ranked order.

        Two lanes:
        - API lane: known-ATS companies (pure HTTP) scanned in parallel
          worker threads (config: scan_parallelism.api_workers, default 12).
        - Browser lane: everything else, sequential in this thread (the
          Playwright sync browser must stay on its owning thread).

        Active/dormant tiering (scan_scheduler): chronically zero-yield
        companies are only scanned every Nth cycle. Disabled for targeted
        rescans, which always run.
        """
        sorted_companies, _ = rank_companies()

        if target_company_id:
            sorted_companies = [c for c in sorted_companies if c.get("id") == target_company_id]
            if not sorted_companies:
                print(f"Company {target_company_id} not found.")
                return

        config = load_json(CONFIG_FILE, {})
        jobs_store = load_json(JOBS_FILE, {"jobs": []})
        existing_jobs = {j["id"]: j for j in jobs_store.get("jobs", [])}
        metrics_store = load_json(METRICS_FILE, {"companies": {}})
        companies_data = load_json(COMPANIES_FILE, {"companies": []})
        comp_map = {c["id"]: c for c in companies_data.get("companies", [])}

        # Active/dormant tiering (full cycles only, never targeted rescans)
        skipped_dormant = []
        if not target_company_id:
            cycle_no = next_cycle_number(metrics_store)
            sorted_companies, skipped_dormant = partition_companies(
                sorted_companies, metrics_store, config, cycle_no)
            if skipped_dormant:
                every_n = (config.get("scan_tiering") or {}).get("dormant_every_n_cycles", 4)
                print(f"[ScanScheduler] Cycle {cycle_no}: skipping {len(skipped_dormant)} dormant companies "
                      f"(rechecked every {every_n} cycles).")

        print(f"Starting scan for {len(sorted_companies)} companies...")

        state_lock = threading.Lock()

        def scan_one(company, in_worker_thread):
            cid = company["id"]
            cname = company["name"]
            if company.get("bot_protected"):
                print(f"Skipping [{cid}]: {cname} (Bot protected - WAF/Bot Manager challenge).")
                return
            print(f"Scanning [{cid}]: {cname}...")

            stored_pattern = self.pattern_store.get_pattern(cid)

            # Network scan: outside the lock (this is the parallel part).
            # Worker threads only ever reach the requests-based ATS
            # extractors (guaranteed by lane assignment via _is_api_scannable).
            try:
                jobs, learned_pattern, method, error_msg = self.scanner.scan_company(company, stored_pattern)
            except Exception as scan_err:
                print(f"Error scanning [{cid}]: {cname}: {scan_err}")
                jobs, learned_pattern, method, error_msg = [], None, "heuristic", str(scan_err)

            now_iso = datetime.now().isoformat()
            is_success = len(jobs) > 0 and error_msg is None

            with state_lock:
                prev_m = metrics_store["companies"].get(cid, {})
                prev_scans = prev_m.get("total_scans", 0)
                prev_extracted = prev_m.get("jobs_extracted", 0)
            hist_avg = (prev_extracted / prev_scans) if prev_scans > 0 else 0.0

            parse_conf_score = compute_parse_confidence_score(company, jobs, method, hist_avg)
            scan_thresh = get_scan_confidence_threshold()
            cross_check_note = None

            if parse_conf_score < scan_thresh:
                try:
                    from relevance_predictor import RelevancePredictor
                    from cycle_yield_tracker import CycleYieldTracker
                    from llm_router import LLMRouter

                    rel_pred = RelevancePredictor()
                    yield_tracker = CycleYieldTracker()
                    llm_router = LLMRouter()

                    comp_relevance = rel_pred.predict_relevance(cname, "")
                    current_h = datetime.now().hour
                    yield_mult = yield_tracker.get_yield_multiplier(current_h)
                    headroom_info = llm_router.get_quota_headroom_info(current_h)
                    quota_low = headroom_info.get("is_low", False)

                    should_trigger_apify = True
                    gating_reason = ""

                    if quota_low and comp_relevance < 0.85:
                        should_trigger_apify = False
                        gating_reason = f"LLM quota headroom low - skipping Apify for [{cname}]"
                    elif yield_mult < 0.8 and comp_relevance < 0.70:
                        should_trigger_apify = False
                        gating_reason = f"Low-yield cycle ({yield_mult}x) requires HIGH company relevance (>=0.70), current relevance is {comp_relevance:.2f} - skipping Apify"
                    elif comp_relevance < 0.50:
                        should_trigger_apify = False
                        gating_reason = f"Low parse confidence ({parse_conf_score:.2f}) but company relevance is low ({comp_relevance:.2f}) - skipping Apify"
                    else:
                        gating_reason = f"Apify triggered - parse confidence low ({parse_conf_score:.2f}) and company relevance qualifying ({comp_relevance:.2f})"

                    if should_trigger_apify:
                        print(f"Low parse confidence ({parse_conf_score:.2f} < threshold {scan_thresh:.2f}) for [{cid}]: {cname}. Triggering Apify fallback... ({gating_reason})")
                        apify_scanner = ApifyScanner()
                        apify_jobs, apify_conf, _ = apify_scanner.scan_company_via_apify(company)
                        if apify_jobs and (len(apify_jobs) > len(jobs) or len(jobs) == 0):
                            jobs = apify_jobs
                            method = "apify"
                            error_msg = None
                            is_success = True
                            parse_conf_score = apify_conf
                            cross_check_note = f"Apify fallback selected ({len(apify_jobs)} jobs extracted) [{gating_reason}]"
                            with state_lock:
                                record_outcome("scan_confidence_threshold", success=True)
                        else:
                            cross_check_note = f"confirmed by Apify cross-check [{gating_reason}]"
                            with state_lock:
                                record_outcome("scan_confidence_threshold", success=True)
                    else:
                        cross_check_note = f"Self-built result retained: {gating_reason}"
                        print(f"[{cname}] {cross_check_note}")
                except ApifyUnavailableError as a_err:
                    cross_check_note = f"Apify unavailable, using self-built result at lower confidence ({a_err})"
                    print(f"[{cname}] {cross_check_note}")
                    with state_lock:
                        record_outcome("scan_confidence_threshold", success=False)
                except Exception as ex:
                    cross_check_note = f"Apify error: {ex}"
                    with state_lock:
                        record_outcome("scan_confidence_threshold", success=False)

            # All shared-state bookkeeping under one lock.
            with state_lock:
                if is_success and learned_pattern and method in ("heuristic", "llm_learned"):
                    self.pattern_store.save_pattern(
                        cid,
                        learned_pattern.get("job_card_selector", ""),
                        learned_pattern.get("title_selector", ""),
                        learned_pattern.get("location_selector", ""),
                        learned_pattern.get("apply_link_selector", "")
                    )
                elif not is_success and stored_pattern:
                    self.pattern_store.record_failure(cid)

                m_comp = metrics_store["companies"].get(cid, {
                    "total_scans": 0,
                    "successful_scans": 0,
                    "jobs_extracted": 0,
                    "parsing_accuracy": 0.0,
                    "last_scan": None,
                    "extraction_method": method,
                    "errors": []
                })

                m_comp["total_scans"] += 1
                if is_success:
                    m_comp["successful_scans"] += 1
                    m_comp["jobs_extracted"] += len(jobs)
                m_comp["parsing_accuracy"] = round(m_comp["successful_scans"] / m_comp["total_scans"], 2)
                m_comp["last_scan"] = now_iso
                m_comp["extraction_method"] = method
                m_comp["parse_confidence_score"] = parse_conf_score
                m_comp["cross_check_note"] = cross_check_note
                if error_msg:
                    m_comp["errors"].append(error_msg)
                update_yield_streak(m_comp, len(jobs), now_iso)

                # Fresher-yield tracking: the user's goal is a list where
                # 70-80% of companies are actively hiring freshers, so we
                # measure that per company instead of assuming it.
                try:
                    from company_discovery import is_fresher_title
                    fresher_now = sum(1 for j in jobs if is_fresher_title(j.get("title", "")))
                except Exception:
                    fresher_now = 0
                m_comp["fresher_jobs_last_scan"] = fresher_now
                m_comp["fresher_jobs_total"] = m_comp.get("fresher_jobs_total", 0) + fresher_now
                if fresher_now > 0:
                    m_comp["last_fresher_at"] = now_iso
                    m_comp["fresher_zero_streak"] = 0
                else:
                    m_comp["fresher_zero_streak"] = m_comp.get("fresher_zero_streak", 0) + 1

                metrics_store["companies"][cid] = m_comp

                update_company_difficulty(cid, len(jobs), is_success)

                if cid in comp_map:
                    comp_map[cid]["parsed_count"] = comp_map[cid].get("parsed_count", 0) + len(jobs)
                    comp_map[cid]["parsing_accuracy"] = m_comp["parsing_accuracy"]
                    comp_map[cid]["last_parsed"] = now_iso
                    # Promote to the parallel API lane for future cycles: if
                    # this scan ended up on a direct ATS API (e.g. the browser
                    # followed a redirect to myworkdayjobs.com), persist that
                    # discovery so the next cycle skips the browser entirely.
                    if is_success and method.endswith("_api"):
                        discovered_ats = method[:-4]  # strip '_api'
                        if (comp_map[cid].get("ats") or "").lower() not in _API_ATS_TYPES:
                            comp_map[cid]["ats"] = discovered_ats
                            print(f"[ScanCoordinator] Learned ATS for {cname}: {discovered_ats} (fast lane next cycle)")

                for job in jobs:
                    jid = job["id"]
                    if jid not in existing_jobs:
                        existing_jobs[jid] = job
                    else:
                        existing_match = existing_jobs[jid].get("match")
                        job_copy = dict(job)
                        if existing_match and not job_copy.get("match"):
                            job_copy["match"] = existing_match
                        existing_jobs[jid] = job_copy

                # Merge-safe store save: merge OUR view into a freshly loaded
                # store instead of overwriting it. Rules: keep jobs other
                # writers added; keep our job data for jobs we scanned; but
                # never replace a fresh match with a missing one (the
                # rescorer may have scored a job after our snapshot).
                fresh = load_json(JOBS_FILE, {"jobs": []})
                merged_by_id = {j["id"]: j for j in fresh.get("jobs", []) if j.get("id")}
                for jid, job in existing_jobs.items():
                    fresh_job = merged_by_id.get(jid)
                    if fresh_job is not None:
                        fresh_match = fresh_job.get("match")
                        our_match = job.get("match")
                        fresh_is_dict = isinstance(fresh_match, dict)
                        our_is_dict = isinstance(our_match, dict)
                        # adopt the store's match when ours is missing, OR the
                        # store's is from a better (paid LLM, tier 1/2) tier —
                        # never downgrade a refined score with a stale cheap one
                        fresh_better = fresh_is_dict and (
                            not our_is_dict
                            or (fresh_match.get("tier") in (1, 2) and our_match.get("tier") not in (1, 2))
                        )
                        if fresh_better:
                            job = dict(job)
                            job["match"] = fresh_match
                            existing_jobs[jid] = job
                    merged_by_id[jid] = job
                jobs_store["jobs"] = list(merged_by_id.values())
                save_json(JOBS_FILE, jobs_store)
                save_json(METRICS_FILE, metrics_store)
                companies_data["companies"] = list(comp_map.values())
                save_json(COMPANIES_FILE, companies_data)

            print(f"Finished {cname}: Extracted {len(jobs)} jobs (Success: {is_success}, Method: {method})")

        try:
            api_ids = {c["id"] for c in sorted_companies if _is_api_scannable(c) and not c.get("bot_protected")}
            api_lane = [c for c in sorted_companies if c["id"] in api_ids]
            browser_lane = [c for c in sorted_companies if c["id"] not in api_ids]

            workers = int((config.get("scan_parallelism") or {}).get("api_workers", 12))
            workers = max(1, min(workers, 32))
            print(f"[ScanCoordinator] API lane: {len(api_lane)} companies across {workers} workers; "
                  f"browser lane: {len(browser_lane)} sequential.")

            if api_lane:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = [executor.submit(scan_one, c, True) for c in api_lane]
                    for f in futures:
                        f.result()

            # Browser lane stays on this thread: Playwright's sync API is
            # not thread-safe and the browser belongs to this thread.
            for company in browser_lane:
                scan_one(company, False)

        finally:
            self.scanner.close()

        # Score jobs (single pass; HybridJobScorer skips jobs already scored
        # against the current resume version, so this is incremental).
        resume_data = load_json(os.path.join(BASE_DIR, "resume_store.json"), {})
        if resume_data.get("has_resume"):
            print("Deduplicating & scoring extracted jobs with HybridJobScorer...")
            try:
                from job_deduplicator import JobDeduplicator
                deduplicator = JobDeduplicator()
                all_raw_jobs = list(existing_jobs.values())
                deduped_jobs, _ = deduplicator.deduplicate(all_raw_jobs)

                from hybrid_scorer import HybridJobScorer
                scorer = HybridJobScorer(resume_data)
                for job in deduped_jobs:
                    job["match"] = scorer.score_job(job)

                existing_jobs = {j["id"]: j for j in deduped_jobs}
            except Exception as e:
                print(f"Error scoring jobs: {e}")

        # Final save: merge into a freshly loaded store by job id, never
        # replacing a fresh match with a missing one (same rules as the
        # incremental saves above).
        fresh_store = load_json(JOBS_FILE, {"jobs": []})
        final_by_id = {j["id"]: j for j in fresh_store.get("jobs", []) if j.get("id")}
        for jid, job in existing_jobs.items():
            fresh_job = final_by_id.get(jid)
            if fresh_job is not None:
                fresh_match = fresh_job.get("match")
                our_match = job.get("match")
                fresh_better = isinstance(fresh_match, dict) and (
                    not isinstance(our_match, dict)
                    or (fresh_match.get("tier") in (1, 2) and our_match.get("tier") not in (1, 2))
                )
                if fresh_better:
                    job = dict(job)
                    job["match"] = fresh_match
            final_by_id[jid] = job
        fresh_store["jobs"] = list(final_by_id.values())
        save_json(JOBS_FILE, fresh_store)
        save_json(METRICS_FILE, metrics_store)
        companies_data["companies"] = list(comp_map.values())
        save_json(COMPANIES_FILE, companies_data)

        # Phase 3 Auto-Improvement Loop
        try:
            from threshold_optimizer import analyze_and_optimize
            from pattern_recognizer import generate_recommendations
            adjs = analyze_and_optimize()
            recs = generate_recommendations()
            print(f"Auto-improvement check: {len(adjs)} threshold adjustments, {len(recs)} recommendations generated.")
        except Exception as e:
            print(f"Error running auto-improvement loop: {e}")

        # Regenerate scan rankings with fresh data
        rank_companies()
        print("Scan cycle completed successfully!")

if __name__ == "__main__":
    coordinator = ScanCoordinator()
    coordinator.run_scan()
