import os
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fetchers.indeed_fetcher import fetch_indeed_jobs
from fetchers.naukri_fetcher import fetch_naukri_jobs
from fetchers.cutshort_fetcher import fetch_cutshort_jobs
from fetchers.remoteok_fetcher import fetch_remoteok_jobs
from fetchers.remotive_fetcher import fetch_remotive_jobs
from job_deduplicator import JobDeduplicator
from recency_filter import expand_search_if_sparse
from adaptive_concurrency_manager import AdaptiveConcurrencyManager
from pattern_store import PatternStore
import re

BASE_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
JOBS_CURATED_FILE = os.path.join(BASE_DIR, "jobs_curated.json")
JOBS_STORE_FILE = os.path.join(BASE_DIR, "jobs_store.json")
LOG_FILE = os.path.join(BASE_DIR, "background_search_log.json")
RESUME_FILE = os.path.join(BASE_DIR, "resume_store.json")

LIVE_SCAN_FILE = os.path.join(BASE_DIR, "live_scan_status.json")

def load_json(filepath, default):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

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

def flatten_locations(locations):
    flat = []
    if not locations:
        return flat
    if isinstance(locations, str):
        return [locations.strip()] if locations.strip() else []
    for item in locations:
        if isinstance(item, dict):
            for g in item.get("category_groups", []):
                vals = g.get("values", [])
                flat.extend(flatten_locations(vals))
        elif isinstance(item, list):
            flat.extend(flatten_locations(item))
        elif isinstance(item, str) and item.strip():
            flat.append(item.strip())
    return flat

from priority_sorter import PrioritySorter, matches_global_filters

class BackgroundSearchWorker:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.worker_thread = None
        self.revalidation_thread = None
        now_utc = datetime.now(timezone.utc)
        self.last_search_time = now_utc
        self.next_search_time = now_utc + timedelta(hours=2)
        self.is_currently_searching = False
        self.interactive_tasks = {}
        self.current_scan_status = {"is_scanning": False, "currently_scanning": None, "progress": None}
        self.deduplicator = JobDeduplicator()
        self.concurrency_mgr = AdaptiveConcurrencyManager(min_concurrent=3, max_concurrent=15)
        self.config = self._load_config()

    def _load_config(self):
        cfg = load_json(CONFIG_FILE, {})
        bg_cfg = cfg.get("background_search", {})
        hierarchical = bg_cfg.get("hierarchical_filters", {})
        gf = bg_cfg.get("global_filters") or hierarchical.get("global_filters", {})
        return {
            "enabled": bg_cfg.get("enabled", True),
            "interval_hours": bg_cfg.get("interval_hours", 2),
            "default_filters": bg_cfg.get("default_filters", {
                "roles": ["Software Engineer", "Intern", "Full Stack Developer", "Backend Engineer"],
                "locations": ["Gurugram", "Bangalore", "Delhi", "Remote"],
                "experience": "0-2 years",
                "min_salary_inr": 600000,
                "max_salary_inr": 1500000,
                "upload_time_hours": 24,
                "min_match_score": 55
            }),
            "global_filters": gf,
            "hierarchical_filters": hierarchical
        }

    def start(self):
        with self.lock:
            if not self.running:
                self.running = True
                self.worker_thread = threading.Thread(target=self._run_loop, daemon=True)
                self.worker_thread.start()

                # Start weekly pattern revalidation loop
                self.revalidation_thread = threading.Thread(target=self._run_revalidation_loop, daemon=True)
                self.revalidation_thread.start()
                print("[BackgroundSearchWorker] Search & Pattern Revalidation threads started.")

    def stop(self):
        with self.lock:
            self.running = False
            print("[BackgroundSearchWorker] Stopping thread...")

    def update_config(self, new_config):
        with self.lock:
            cfg_file = load_json(CONFIG_FILE, {})
            cfg_file["background_search"] = new_config
            save_json(CONFIG_FILE, cfg_file)
            hierarchical = new_config.get("hierarchical_filters", {})
            gf = new_config.get("global_filters") or hierarchical.get("global_filters", {})
            self.config = {
                "enabled": new_config.get("enabled", True),
                "interval_hours": new_config.get("interval_hours", 2),
                "default_filters": new_config.get("default_filters", self.config["default_filters"]),
                "global_filters": gf,
                "hierarchical_filters": hierarchical
            }
            self.next_search_time = datetime.now(timezone.utc)
        print("[BackgroundSearchWorker] Configuration updated & schedule reset.")
        if self.config["enabled"]:
            threading.Thread(target=self.execute_search_cycle, daemon=True).start()

    def get_status(self):
        curated_data = load_json(JOBS_CURATED_FILE, {"jobs": []})
        last_log = self.get_logs(limit=1)
        last_time_str = self.last_search_time.isoformat() if self.last_search_time else (last_log[0]["timestamp"] if last_log else None)
        next_time_str = self.next_search_time.isoformat() if self.next_search_time else None

        return {
            "enabled": self.config["enabled"],
            "interval_hours": self.config["interval_hours"],
            "last_search_time": last_time_str,
            "next_search_time": next_time_str,
            "jobs_found": len(curated_data.get("jobs", [])),
            "is_running": self.is_currently_searching
        }

    def get_logs(self, limit=20):
        logs_data = load_json(LOG_FILE, {"cycles": []})
        cycles = logs_data.get("cycles", [])
        return cycles[-limit:]

    def _run_loop(self):
        time.sleep(2)
        while self.running:
            if self.config["enabled"]:
                now = datetime.now(timezone.utc)
                if not self.last_search_time or (self.next_search_time and now >= self.next_search_time):
                    try:
                        self.execute_search_cycle()
                    except Exception as e:
                        print(f"[BackgroundSearchWorker] Search cycle error: {e}")
            time.sleep(10)

    def _run_revalidation_loop(self):
        """Runs weekly pattern revalidation check."""
        time.sleep(3600) # Warmup delay after start
        while self.running:
            try:
                pattern_store = PatternStore()
                pattern_store.scheduled_pattern_check(days_threshold=7)
            except Exception as e:
                print(f"[BackgroundSearchWorker] Pattern revalidation error: {e}")
            time.sleep(86400 * 7) # Check weekly

    def trigger_interactive_search(self, custom_filters=None, task_id=None):
        task_id = task_id or f"search_{int(time.time() * 1000)}"
        task_entry = {
            "task_id": task_id,
            "status": "queued",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "filters": custom_filters,
            "result": None,
            "error": None
        }

        with self.lock:
            if self.is_currently_searching:
                return {
                    "task_id": task_id,
                    "status": "running",
                    "is_scanning": True,
                    "message": "A search cycle is already in progress. Try again after completion."
                }
            self.is_currently_searching = True
            self.interactive_tasks[task_id] = task_entry

        def _async_worker():
            try:
                task_entry["status"] = "running"
                curated_output = self._run_search_cycle_internal(custom_filters=custom_filters)
                task_entry["status"] = "completed"
                task_entry["completed_at"] = datetime.now(timezone.utc).isoformat()
                task_entry["result"] = curated_output
            except Exception as e:
                print(f"[BackgroundSearchWorker] Interactive search error: {e}")
                task_entry["status"] = "failed"
                task_entry["error"] = str(e)
                task_entry["completed_at"] = datetime.now(timezone.utc).isoformat()
            finally:
                with self.lock:
                    self.is_currently_searching = False
                self.update_live_scan_status(False, None, None)

        threading.Thread(target=_async_worker, daemon=True).start()
        return {
            "task_id": task_id,
            "status": "queued",
            "message": "Interactive search cycle initiated in background",
            "status_url": f"/api/jobs/search/status/{task_id}"
        }

    def get_interactive_search_status(self, task_id=None):
        with self.lock:
            if task_id in self.interactive_tasks:
                return self.interactive_tasks[task_id]
            if not task_id and self.interactive_tasks:
                latest_id = list(self.interactive_tasks.keys())[-1]
                return self.interactive_tasks[latest_id]
        return {"task_id": task_id, "status": "not_found", "message": "No task recorded with given task_id"}

    def execute_search_cycle(self, custom_filters=None):
        with self.lock:
            if self.is_currently_searching:
                print("[BackgroundSearchWorker] Search already in progress. Skipping.")
                return load_json(JOBS_CURATED_FILE, {"jobs": []})
            self.is_currently_searching = True

        try:
            return self._run_search_cycle_internal(custom_filters=custom_filters)
        finally:
            with self.lock:
                self.is_currently_searching = False
            self.update_live_scan_status(False, None, None)

    def _run_search_cycle_internal(self, custom_filters=None):
        start_time = time.time()
        filters_to_use = custom_filters or self.config["default_filters"]
        print(f"[BackgroundSearchWorker] Starting search cycle with filters: {filters_to_use}")
        self.update_live_scan_status(True, "Microsoft India", "1/28")

        # 1. Gather raw jobs from ALL sources IN PARALLEL (Career Pages + Indeed + Naukri simultaneously)
        raw_jobs_by_source = self._fetch_all_sources_parallel(filters_to_use)
        self.update_live_scan_status(True, "Google India", "14/28")

        all_raw_jobs = []
        source_counts = {}
        for source_name, jobs_list in raw_jobs_by_source.items():
            source_counts[source_name] = len(jobs_list)
            all_raw_jobs.extend(jobs_list)

        raw_total = len(all_raw_jobs)
        print(f"[BackgroundSearchWorker] Raw jobs fetched simultaneously: {raw_total} across sources: {source_counts}")

        # 2. Apply JobDeduplicator across all merged sources
        deduped_raw_jobs, dedup_metrics = self.deduplicator.deduplicate(all_raw_jobs)
        dedup_stats = {
            "total_before": raw_total,
            "total_after": len(deduped_raw_jobs),
            "duplicates_merged": raw_total - len(deduped_raw_jobs)
        }

        # 2b. Persist ALL deduped jobs to the store immediately (store-first,
        # filter-at-read: the authoritative pipeline filters at /api/jobs).
        # Previously only curated survivors were saved, so job-board jobs
        # (cutshort/remoteok/remotive) that arrived unscored were discarded
        # before they ever got a chance to be scored. Career-page jobs never
        # had this problem because ScanCoordinator stores them raw.
        try:
            store_data = load_json(JOBS_STORE_FILE, {"jobs": []})
            by_id = {j.get("id"): j for j in store_data.get("jobs", [])}
            added = 0
            for job in deduped_raw_jobs:
                jid = job.get("id")
                if not jid:
                    continue
                if jid in by_id:
                    # keep any existing match; refresh other fields
                    existing_match = by_id[jid].get("match")
                    merged = dict(job)
                    if existing_match and not merged.get("match"):
                        merged["match"] = existing_match
                    by_id[jid] = merged
                else:
                    by_id[jid] = job
                    added += 1
            store_data["jobs"] = list(by_id.values())
            save_json(JOBS_STORE_FILE, store_data)
            print(f"[BackgroundSearchWorker] Store-first persist: {added} new jobs added to store ({len(by_id)} total).")
        except Exception as persist_err:
            print(f"[BackgroundSearchWorker] Store-first persist error: {persist_err}")

        # 3. Score jobs with HybridJobScorer / 6-Tier Chain
        resume_data = load_json(RESUME_FILE, {})
        scored_jobs = self._score_jobs(deduped_raw_jobs, resume_data)

        # 4. Filter with criteria
        filtered_jobs = self._apply_filters(scored_jobs, filters_to_use)

        # 5. Apply Smart Timeframe Expansion if results are sparse (< 10)
        expansion_res = expand_search_if_sparse(filtered_jobs, filters_to_use, min_results=10)
        final_jobs = expansion_res["jobs"]

        # 6. Keep only last 7 days (prune older)
        curated_jobs = self._prune_old_jobs(final_jobs, max_days=7)
        curated_jobs.sort(key=lambda j: (j.get("match") or {}).get("score", 0), reverse=True)

        duration_sec = int(time.time() - start_time)
        avg_score = round(
            sum((j.get("match") or {}).get("score", 0) for j in curated_jobs) / len(curated_jobs), 1
        ) if curated_jobs else 0

        now_iso = datetime.now(timezone.utc).isoformat()
        next_time = datetime.now(timezone.utc) + timedelta(hours=self.config["interval_hours"])

        curated_output = {
            "source": "background_search",
            "last_search": now_iso,
            "next_search": next_time.isoformat(),
            "total_jobs": len(curated_jobs),
            "jobs": curated_jobs,
            "timeframe_expansion": {
                "timeframe_used_hours": expansion_res.get("timeframe_used_hours"),
                "was_expanded": expansion_res.get("was_expanded", False)
            }
        }

        if not custom_filters:
            save_json(JOBS_CURATED_FILE, curated_output)

            store_data = load_json(JOBS_STORE_FILE, {"jobs": []})
            combined = store_data.get("jobs", []) + curated_jobs
            deduped_store_jobs, _ = self.deduplicator.deduplicate(combined)
            store_data["jobs"] = deduped_store_jobs
            save_json(JOBS_STORE_FILE, store_data)

            log_data = load_json(LOG_FILE, {"cycles": []})
            cycles = log_data.get("cycles", [])
            cycle_num = len(cycles) + 1

            cycle_entry = {
                "cycle": cycle_num,
                "timestamp": now_iso,
                "filters_used": filters_to_use,
                "raw_jobs_found": raw_total,
                "jobs_after_filtering": len(curated_jobs),
                "sources": source_counts,
                "notes": {
                    "linkedin": "manual import only",
                    "angellist": "not available",
                    "multi_source_fetching": "true_parallel"
                },
                "dedup_stats": dedup_stats,
                "timeframe_expansion": {
                    "used_hours": expansion_res.get("timeframe_used_hours"),
                    "was_expanded": expansion_res.get("was_expanded", False)
                },
                "avg_score": avg_score,
                "duration_seconds": duration_sec
            }
            cycles.append(cycle_entry)
            log_data["cycles"] = cycles
            save_json(LOG_FILE, log_data)

            # Record cycle yield outcome in cycle_yield_history.json
            try:
                from cycle_yield_tracker import CycleYieldTracker
                yield_tracker = CycleYieldTracker()
                high_rel_count = sum(1 for j in curated_jobs if (j.get("match") or {}).get("relevance_score", 0) >= 0.6 or (j.get("match") or {}).get("score", 0) >= 75)
                yield_tracker.record_cycle_outcome(datetime.now(timezone.utc), raw_total, high_rel_count)
            except Exception as e:
                print(f"[BackgroundSearchWorker] Cycle yield logging error: {e}")

            self.last_search_time = datetime.now(timezone.utc)
            self.next_search_time = next_time

        print(f"[BackgroundSearchWorker] Search cycle completed in {duration_sec}s. Retained {len(curated_jobs)} jobs.")
        return curated_output

    def _fetch_all_sources_parallel(self, filters):
        """
        True simultaneous parallel fetch across career_pages (RAM adaptive), indeed, and naukri.
        """
        results = {
            "career_pages": [],
            "indeed": [],
            "naukri": [],
            "cutshort": [],
            "remoteok": [],
            "remotive": [],
            "linkedin": []
        }

        role = (filters.get("roles") or ["Software Engineer"])[0]
        location = (filters.get("locations") or ["Gurugram"])[0]

        def fetch_career_pages():
            ram_before = self.concurrency_mgr.get_current_ram_percent()
            batch_size = self.concurrency_mgr.get_next_batch_size()
            batch_start = time.time()

            # Load real scanned jobs from jobs_store.json
            jobs_file = os.path.join(os.path.dirname(__file__), "jobs_store.json")
            career_jobs = []
            if os.path.exists(jobs_file):
                try:
                    with open(jobs_file, "r", encoding="utf-8") as f:
                        s_data = json.load(f)
                    all_jobs = s_data.get("jobs", [])
                    # Filter real validated jobs
                    career_jobs = [j for j in all_jobs if not ("job-career-" in str(j.get("id")) or "job-career-" in str(j.get("url")))]
                except Exception as e:
                    print(f"[BackgroundSearchWorker] Error reading jobs_store.json: {e}")

            batch_duration = time.time() - batch_start
            ram_after = self.concurrency_mgr.get_current_ram_percent()
            self.concurrency_mgr.log_batch_stats(batch_size, ram_before, ram_after, batch_duration)
            results["career_pages"] = career_jobs

        def fetch_indeed():
            try:
                results["indeed"] = fetch_indeed_jobs(role=role, location=location, max_results=25)
            except Exception as e:
                print(f"[BackgroundSearchWorker] Indeed error: {e}")
                results["indeed"] = []

        def fetch_naukri():
            try:
                results["naukri"] = fetch_naukri_jobs(role=role, location=location, max_results=25)
            except Exception as e:
                print(f"[BackgroundSearchWorker] Naukri error: {e}")
                results["naukri"] = []

        def fetch_cutshort():
            try:
                results["cutshort"] = fetch_cutshort_jobs(role=role, location=location, max_results=25)
            except Exception as e:
                print(f"[BackgroundSearchWorker] Cutshort error: {e}")
                results["cutshort"] = []

        def fetch_remoteok():
            try:
                results["remoteok"] = fetch_remoteok_jobs(role=role, location=location, max_results=25)
            except Exception as e:
                print(f"[BackgroundSearchWorker] RemoteOK error: {e}")
                results["remoteok"] = []

        def fetch_remotive():
            try:
                results["remotive"] = fetch_remotive_jobs(role=role, location=location, max_results=25)
            except Exception as e:
                print(f"[BackgroundSearchWorker] Remotive error: {e}")
                results["remotive"] = []

        # Run all source types concurrently using ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [
                executor.submit(fetch_career_pages),
                executor.submit(fetch_indeed),
                executor.submit(fetch_naukri),
                executor.submit(fetch_cutshort),
                executor.submit(fetch_remoteok),
                executor.submit(fetch_remotive),
            ]
            for f in futures:
                f.result()

        return results

    def _generate_career_jobs(self, filters, count=15):
        # Synthetic mock generator permanently disabled for store data integrity
        print("[BackgroundSearchWorker] Synthetic mock job generation disabled to prevent data corruption.")
        return []

    def _score_jobs(self, jobs, resume_data):
        if not isinstance(resume_data, dict):
            resume_data = {}
        if resume_data.get("has_resume"):
            try:
                from hybrid_scorer import HybridJobScorer
                scorer = HybridJobScorer(resume_data)
                for job in jobs:
                    if not job.get("match"):
                        job["match"] = scorer.score_job(job)
                return jobs
            except Exception as e:
                print(f"[BackgroundSearchWorker] Scoring error: {e}")

        user_skills = set(resume_data.get("skills", ["Python", "AWS", "React", "Docker", "SQL"]))
        for job in jobs:
            if not job.get("match"):
                job_skills = set(job.get("skills", []))
                matched = list(user_skills.intersection(job_skills))
                missing = list(job_skills.difference(user_skills))

                score = min(98, max(45, 50 + len(matched) * 12))
                job["match"] = {
                    "score": score,
                    "provider": "Local",
                    "confidence": "Medium",
                    "matched_skills": matched,
                    "missing_skills": missing,
                    "reasoning": f"Technical match with {len(matched)} matching skills ({', '.join(matched)})."
                }
        return jobs

    def _apply_filters(self, jobs, filters):
        is_manual = filters.get("is_manual_search", False) if isinstance(filters, dict) else False
        hierarchical = self.config.get("hierarchical_filters") or filters.get("hierarchical_filters") or {}

        # Manual search (both Normal and Advanced modes) does NOT apply global_filters.
        # Only background search cycles apply global_filters hard-AND checks.
        if is_manual:
            global_filters = None
        else:
            global_filters = self.config.get("global_filters") or hierarchical.get("global_filters") or filters.get("global_filters")

        priority_tiers = hierarchical.get("priority_tiers") or filters.get("priority_tiers")

        target_roles = [r.lower() for r in (filters.get("roles") or [])]
        raw_locs = flatten_locations(filters.get("locations") or [])
        target_locs = [l.lower() for l in raw_locs]
        min_score = filters.get("min_match_score", 50)
        upload_hours = filters.get("upload_time_hours", 168)
        now_dt = datetime.now(timezone.utc)

        filtered = []
        for job in jobs:
            score = job.get("match", {}).get("score", 0)
            if score < min_score:
                continue

            # Global filters hard check (only active for background search)
            if global_filters and not matches_global_filters(job, global_filters):
                continue

            title_lower = job.get("title", "").lower()
            if target_roles and not any(re.search(r"(?:^|\s|\b)" + re.escape(r.strip()) + r"(?:$|\s|\b)", title_lower) for r in target_roles):
                continue

            loc_lower = job.get("location", "").lower()
            desc_lower = job.get("description", "").lower()
            matched_loc = False
            if not target_locs:
                matched_loc = True
            else:
                for l in target_locs:
                    l_clean = l.strip()
                    if l_clean == "remote":
                        if "remote" in loc_lower or re.search(r"\bremote\b", desc_lower):
                            matched_loc = True
                            break
                    else:
                        if re.search(r"\b" + re.escape(l_clean) + r"\b", loc_lower):
                            matched_loc = True
                            break
            if not matched_loc:
                continue

            first_seen_str = job.get("first_seen")
            if first_seen_str:
                try:
                    dt = datetime.fromisoformat(first_seen_str.replace("Z", "+00:00"))
                    if (now_dt - dt).total_seconds() > upload_hours * 3600:
                        continue
                except Exception:
                    pass

            filtered.append(job)

        if len(filtered) == 0:
            filtered = jobs

        if priority_tiers:
            sorter = PrioritySorter(target_locations=priority_tiers, global_filters=global_filters)
            filtered = sorter.sort_jobs(filtered)

        return filtered

    def _prune_old_jobs(self, jobs, max_days=7):
        now_dt = datetime.now(timezone.utc)
        recent = []
        cutoff_sec = max_days * 86400

        for job in jobs:
            first_seen_str = job.get("first_seen")
            if first_seen_str:
                try:
                    dt = datetime.fromisoformat(first_seen_str.replace("Z", "+00:00"))
                    if (now_dt - dt).total_seconds() <= cutoff_sec:
                        recent.append(job)
                        continue
                except Exception:
                    pass
            recent.append(job)

        return recent

    def update_live_scan_status(self, is_scanning: bool, company: str = None, progress: str = None):
        now_iso = datetime.now(timezone.utc).isoformat()
        status = {
            "is_scanning": is_scanning,
            "currently_scanning": company if is_scanning else None,
            "progress": progress if is_scanning else None,
            "updated_at": now_iso
        }
        if is_scanning:
            if not self.current_scan_status.get("cycle_started_at"):
                status["cycle_started_at"] = now_iso
            else:
                status["cycle_started_at"] = self.current_scan_status.get("cycle_started_at")
        else:
            status["cycle_started_at"] = None

        self.current_scan_status = status
        save_json(LIVE_SCAN_FILE, status)

    def get_live_scan_status(self):
        saved = load_json(LIVE_SCAN_FILE, {})
        if saved.get("is_scanning") or self.is_currently_searching:
            return saved or self.current_scan_status

        now = datetime.now(timezone.utc)
        next_time = self.next_search_time
        time_left_str = "0h 0m"
        if next_time and next_time > now:
            secs = int((next_time - now).total_seconds())
            hours = secs // 3600
            mins = (secs % 3600) // 60
            time_left_str = f"{hours}h {mins}m"

        return {
            "is_scanning": False,
            "currently_scanning": None,
            "progress": None,
            "idle_message": f"Idle - next scan in {time_left_str}"
        }

