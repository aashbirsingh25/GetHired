import os

# macOS fix: torch (via sentence-transformers) and faiss each bundle their own
# copy of the OpenMP runtime (libomp.dylib). Using both in one process
# segfaults the app (observed: deterministic SIGSEGV on resume upload, the
# first code path exercising both, 6/6 repro runs). Forcing single-threaded
# OpenMP avoids the duplicate-runtime race (verified 8/8 clean runs, incl.
# threaded). KMP_DUPLICATE_LIB_OK was tried first and did NOT help (3/3 still
# crashed). Must be set before torch/faiss are imported. Perf cost is
# negligible for this app's tiny embedding batches.
os.environ.setdefault("OMP_NUM_THREADS", "1")

# Load API keys from the gitignored .env file (GEMINI_API_KEYS, GROQ_API_KEY
# etc.) so LLMRouter finds them. Must happen before llm_router import.
_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_ENV_FILE):
    try:
        with open(_ENV_FILE, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _, _v = _line.partition("=")
                    os.environ.setdefault(_k.strip(), _v.strip())
    except Exception as _env_err:
        print(f"[AppStartup] Warning: could not read .env: {_env_err}")

import json
import csv
import io
import hashlib
import threading
import time
from datetime import datetime, timezone
from flask import Flask, jsonify, request
from werkzeug.utils import secure_filename

from scan_coordinator import ScanCoordinator, load_json, save_json
from company_ranker import rank_companies, load_companies
from resume_parser import parse_resume
from chunking_service import ChunkingService
from embedding_service import EmbeddingService
from vector_store import VectorStoreService
from llm_router import LLMRouter
from hybrid_scorer import HybridJobScorer
from semantic_filters import SemanticFilterEngine
from priority_sorter import PrioritySorter
from recency_filter import filter_by_recency, expand_search_if_sparse
from feedback_system import FeedbackCollector
from threshold_optimizer import analyze_and_optimize, LOG_FILE as AUTO_LOG_FILE
from keyword_learner import learn_from_positive_feedback, learn_from_negative_feedback
from pattern_recognizer import generate_recommendations as get_phase3_recs
from application_tracker import ApplicationTracker
from company_analyzer import CompanyAnalyzer
from role_analyzer import RoleAnalyzer
from location_analyzer import LocationAnalyzer
from timeline_analyzer import TimelineAnalyzer
from insight_generator import InsightGenerator
from insights_aggregator import InsightsAggregator
from recommendation_engine import RecommendationEngine
from dashboard_stats import DashboardStats
from linkedin_manual_import import import_linkedin_job_from_url

from background_search_worker import BackgroundSearchWorker, JOBS_CURATED_FILE
from store_integrity_checker import validate_jobs_store_integrity, enforce_jobs_store_safeguard

app = Flask(__name__, static_folder="static", static_url_path="/static")

# Clean up orphaned atomic-write temp files (*.json.tmp_<pid>_<tid>) left
# behind when the process was killed mid-write. save_json normally removes
# them, but a kill between write and os.replace strands them; two were found
# after the Aug 24 shutdown and two more after Aug 29 restarts.
def _cleanup_stale_tmp_files():
    base = os.path.dirname(os.path.abspath(__file__))
    removed = 0
    now = time.time()
    for fn in os.listdir(base):
        if ".json.tmp_" in fn:
            path = os.path.join(base, fn)
            try:
                # Age guard: an in-flight write from a live process is
                # milliseconds old; only reap files that are clearly orphans.
                if now - os.path.getmtime(path) > 3600:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass
    if removed:
        print(f"[App] Removed {removed} orphaned .tmp_* file(s) from interrupted writes.")

_cleanup_stale_tmp_files()

BASE_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
COMPANIES_FILE = os.path.join(BASE_DIR, "companies.json")
JOBS_FILE = os.path.join(BASE_DIR, "jobs_store.json")

# Startup Store Integrity Safeguard Enforcement
if os.path.exists(JOBS_FILE):
    try:
        with open(JOBS_FILE, "r", encoding="utf-8") as _f:
            _sdata = json.load(_f)
        _raw_jobs = _sdata.get("jobs", [])
        _is_valid, _errs = validate_jobs_store_integrity(_raw_jobs)
        if not _is_valid:
            print(f"[AppStartup] Integrity Safeguard warning: Flagged {len(_errs)} items. Cleaning store...")
            _sdata["jobs"] = enforce_jobs_store_safeguard(_raw_jobs)
            save_json(JOBS_FILE, _sdata)
        else:
            print(f"[AppStartup] Store Integrity Safeguard verified: {len(_raw_jobs)} real jobs.")
    except Exception as _e:
        print(f"[AppStartup] Safeguard check error: {_e}")


def _get_job_match_score(job, default=50):
    if not isinstance(job, dict):
        return default
    match_obj = job.get("match")
    if not isinstance(match_obj, dict):
        return default
    score_val = match_obj.get("score")
    if score_val is None or not isinstance(score_val, (int, float)):
        return default
    return int(score_val)

bg_worker = BackgroundSearchWorker()
bg_worker.start()
METRICS_FILE = os.path.join(BASE_DIR, "metrics.json")
SCAN_ORDER_FILE = os.path.join(BASE_DIR, "scan_order.json")
RESUME_FILE = os.path.join(BASE_DIR, "resume_store.json")
FILTER_METRICS_FILE = os.path.join(BASE_DIR, "filter_metrics.json")
FILTERS_FILE = os.path.join(BASE_DIR, "filters.json")
APPLY_LATER_FILE = os.path.join(BASE_DIR, "apply_later.json")
VIEWED_JOBS_FILE = os.path.join(BASE_DIR, "viewed_jobs.json")

# Cached /api/jobs payload. The feed is expensive to compute (dedupe + filter +
# rank ~16k jobs) and this process also runs the scanner and LLM rescorer, so
# recomputing per request starved the web server into timeouts.
_FEED_CACHE = {"key": None, "payload": None}
_FEED_BUILD_LOCK = threading.Lock()
SAVED_JOBS_FILE = os.path.join(BASE_DIR, "saved_jobs.json")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route("/")
def index():
    return app.send_static_file("index.html")

# Background scheduler thread (legacy scanner)
def background_scanner_loop():
    print("Background scanner loop started...")
    time.sleep(2)
    coordinator = ScanCoordinator()
    try:
        coordinator.run_scan()
    except Exception as e:
        print(f"Error in background scan: {e}")

    while True:
        time.sleep(21600)
        print("Triggering scheduled background scan...")
        try:
            coordinator.run_scan()
        except Exception as e:
            print(f"Error in scheduled background scan: {e}")

@app.route("/api/companies", methods=["GET"])
def get_companies():
    data = load_json(COMPANIES_FILE, {"companies": []})
    return jsonify(data)

@app.route("/api/companies/bulk-import", methods=["POST"])
def bulk_import_companies():
    added = 0
    skipped_duplicates = 0
    invalid_urls = 0

    companies_data = load_json(COMPANIES_FILE, {"companies": []})
    existing_companies = companies_data.get("companies", [])
    
    existing_names = {c.get("name", "").lower().strip() for c in existing_companies}
    existing_urls = {c.get("career_url", "").lower().strip() for c in existing_companies}

    rows_to_process = []

    # 1. Check if multipart CSV file uploaded
    if "file" in request.files:
        file = request.files["file"]
        if file and file.filename != "":
            content = file.read().decode("utf-8", errors="ignore")
            reader = csv.DictReader(io.StringIO(content))
            for row in reader:
                rows_to_process.append({
                    "name": row.get("name") or row.get("Company") or row.get("company_name"),
                    "career_url": row.get("career_url") or row.get("url") or row.get("Career URL"),
                    "ats_hint": row.get("ats_hint") or row.get("ats") or "custom"
                })

    # 2. Check if JSON array body provided
    elif request.is_json:
        body = request.get_json(force=True)
        if isinstance(body, list):
            rows_to_process = body
        elif isinstance(body, dict) and "companies" in body:
            rows_to_process = body["companies"]

    if not rows_to_process:
        return jsonify({"error": "No company data provided. Upload a CSV file or send a JSON array."}), 400

    for item in rows_to_process:
        name = (item.get("name") or "").strip()
        url = (item.get("career_url") or "").strip()
        ats_hint = item.get("ats_hint") or "custom"

        if not name or not url:
            invalid_urls += 1
            continue

        if not (url.startswith("http://") or url.startswith("https://") or "." in url):
            invalid_urls += 1
            continue

        if not (url.startswith("http://") or url.startswith("https://")):
            url = f"https://{url}"

        name_lower = name.lower()
        url_lower = url.lower()

        if name_lower in existing_names or url_lower in existing_urls:
            skipped_duplicates += 1
            continue

        cid = name.lower().replace(" ", "-").replace(".", "")
        new_comp = {
            "id": cid,
            "name": name,
            "career_url": url,
            "ats": ats_hint,
            "difficulty_estimate": 0.8,
            "your_preference_score": 0.70,
            "success_rate": 0,
            "avg_salary_inr": 0,
            "parsed_count": 0,
            "parsing_accuracy": 0,
            "last_parsed": None
        }

        existing_companies.append(new_comp)
        existing_names.add(name_lower)
        existing_urls.add(url_lower)
        added += 1

    companies_data["companies"] = existing_companies
    save_json(COMPANIES_FILE, companies_data)
    rank_companies()

    return jsonify({
        "added": added,
        "skipped_duplicates": skipped_duplicates,
        "invalid_urls": invalid_urls,
        "total_companies_now": len(existing_companies)
    }), 200

@app.route("/api/jobs", methods=["GET"])
def get_jobs():
    from pipeline import execute_authoritative_pipeline

    # /api/jobs used to recompute the whole feed on every request: dedupe,
    # filter and rank ~16k stored jobs. That is a few seconds when the process
    # is idle, but the scan loop and the LLM rescorer run in this same process,
    # so under load requests were starved to the point of TIMING OUT
    # (measured 2026-09-04: 2157s for one call, then hard timeouts, which is
    # why the new UI sat on its spinner forever).
    #
    # Fix: cache the computed payload, keyed on the mtimes of every file that
    # can change the feed. While a build is already in flight, serve the last
    # good payload instead of piling up duplicate work.
    sort_by = request.args.get("sort", "match").lower()
    cache_key = _feed_cache_key(sort_by)

    cached = _FEED_CACHE.get("payload")
    cache_age = time.time() - _FEED_CACHE.get("built_at", 0)
    # Serve the cached payload when nothing changed OR when it is simply
    # recent: the background scanner rewrites jobs_store.json every few
    # seconds, so a pure mtime key would invalidate on almost every request
    # and each page load would pay the full ~17s rebuild. Two minutes of
    # staleness is invisible to the user; the next request after the window
    # rebuilds with fresh data.
    if cached is not None and _FEED_CACHE.get("sort") == sort_by \
            and (_FEED_CACHE.get("key") == cache_key or cache_age < 120):
        return jsonify(cached)

    if not _FEED_BUILD_LOCK.acquire(blocking=False):
        # Someone else is rebuilding. Serving slightly stale data beats making
        # the user wait behind a CPU-bound rebuild.
        if cached is not None:
            stale = dict(cached)
            stale["stale"] = True
            return jsonify(stale)
        _FEED_BUILD_LOCK.acquire()  # nothing to serve yet, so wait for it

    try:
        payload = _build_feed_payload(sort_by)
        _FEED_CACHE["key"] = cache_key
        _FEED_CACHE["sort"] = sort_by
        _FEED_CACHE["built_at"] = time.time()
        _FEED_CACHE["payload"] = payload
        return jsonify(payload)
    finally:
        _FEED_BUILD_LOCK.release()


def _feed_cache_key(sort_by):
    """Invalidate the feed cache when anything that shapes it changes."""
    parts = [sort_by]
    for path in (JOBS_FILE, RESUME_FILE, FILTERS_FILE,
                 APPLY_LATER_FILE, SAVED_JOBS_FILE, VIEWED_JOBS_FILE,
                 os.path.join(BASE_DIR, "applications.json")):
        try:
            parts.append(round(os.path.getmtime(path), 3))
        except OSError:
            parts.append(0)
    return tuple(parts)


def _build_feed_payload(sort_by):
    from pipeline import execute_authoritative_pipeline
    store_data = load_json(JOBS_FILE, {"jobs": []})
    raw_jobs = store_data.get("jobs", [])

    filters_data = load_json(FILTERS_FILE, {})
    resume_data = load_json(RESUME_FILE, {})

    tracker = ApplicationTracker()
    user_apps = tracker.list_applications()
    applied_job_ids = {a.get("job_id") for a in user_apps if a.get("job_id") and a.get("status") != "archived"}
    app_map = {a.get("job_id"): {"app_id": a.get("id"), "status": a.get("status"), "applied_date": a.get("applied_date")} for a in user_apps if a.get("job_id")}

    sort_by = (sort_by or "match").lower()
    filters_data["sort_by"] = sort_by

    pipeline_res = execute_authoritative_pipeline(
        raw_jobs=raw_jobs,
        custom_filters=filters_data,
        resume_data=resume_data,
        applied_job_ids=applied_job_ids
    )

    jobs_list = pipeline_res["jobs"]
    for job in jobs_list:
        jid = job.get("id")
        job["user_application"] = app_map.get(jid)

    pending_jobs = pipeline_res.get("pending_jobs", [])

    status = bg_worker.get_status()

    return {
        "source": "authoritative_store",
        "last_search": status.get("last_search_time") or datetime.now().isoformat(),
        "next_search": status.get("next_search_time") or datetime.now().isoformat(),
        "total_jobs": len(jobs_list),
        "pending_count": len(pending_jobs),
        "filter_breakdown": pipeline_res.get("filter_breakdown"),
        "pipeline_metrics": pipeline_res["metrics"],
        "jobs": jobs_list
    }

@app.route("/api/jobs/add-from-url", methods=["POST"])
def add_job_from_url():
    body = request.get_json(force=True) or {}
    url = body.get("url", "").strip()

    if not url:
        return jsonify({"error": "Missing 'url' parameter"}), 400

    try:
        job_item = import_linkedin_job_from_url(url)
        
        # Score newly imported job
        resume_data = load_json(RESUME_FILE, {})
        if resume_data.get("has_resume"):
            try:
                scorer = HybridJobScorer(resume_data)
                job_item["match"] = scorer.score_job(job_item)
            except Exception:
                pass

        from job_deduplicator import JobDeduplicator
        store_data = load_json(JOBS_FILE, {"jobs": []})
        store_jobs = store_data.get("jobs", [])
        store_jobs.insert(0, job_item)
        
        deduplicator = JobDeduplicator()
        deduped_jobs, _ = deduplicator.deduplicate(store_jobs)
        store_data["jobs"] = deduped_jobs
        save_json(JOBS_FILE, store_data)
        save_json(JOBS_CURATED_FILE, {"last_search": datetime.now().isoformat(), "jobs": deduped_jobs})

        final_job = next((j for j in deduped_jobs if j.get("id") == job_item.get("id")), job_item)
        return jsonify({"message": "Job imported successfully from URL", "job": final_job}), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/jobs/<job_id>/mark-viewed", methods=["POST"])
def mark_job_viewed(job_id):
    viewed_data = load_json(VIEWED_JOBS_FILE, {"viewed_jobs": []})
    viewed_list = viewed_data.get("viewed_jobs", [])
    now_iso = datetime.now().isoformat()

    existing = next((v for v in viewed_list if v.get("job_id") == job_id), None)
    if existing:
        existing["viewed_at"] = now_iso
    else:
        viewed_list.append({
            "job_id": job_id,
            "viewed_at": now_iso
        })

    viewed_data["viewed_jobs"] = viewed_list
    save_json(VIEWED_JOBS_FILE, viewed_data)

    return jsonify({"status": "recorded", "job_id": job_id, "viewed_at": now_iso}), 200

@app.route("/api/jobs/viewed", methods=["GET"])
def get_viewed_jobs():
    viewed_data = load_json(VIEWED_JOBS_FILE, {"viewed_jobs": []})
    viewed_list = viewed_data.get("viewed_jobs", [])

    curated_data = load_json(JOBS_CURATED_FILE, {"jobs": []})
    store_data = load_json(JOBS_FILE, {"jobs": []})
    
    all_jobs_map = {j["id"]: j for j in store_data.get("jobs", [])}
    for j in curated_data.get("jobs", []):
        all_jobs_map[j["id"]] = j

    result_jobs = []
    # Sort viewed list most recent first
    viewed_list_sorted = sorted(viewed_list, key=lambda x: x.get("viewed_at", ""), reverse=True)

    for item in viewed_list_sorted:
        jid = item.get("job_id")
        job = all_jobs_map.get(jid)
        if job:
            job_copy = dict(job)
            job_copy["viewed_at"] = item.get("viewed_at")
            result_jobs.append(job_copy)
        else:
            result_jobs.append({
                "id": jid,
                "title": f"Viewed Job ({jid})",
                "company": "LinkedIn / Partner",
                "location": "India",
                "source": "viewed_history",
                "viewed_at": item.get("viewed_at")
            })

    return jsonify({
        "total_viewed": len(result_jobs),
        "jobs": result_jobs
    })

@app.route("/api/job/<path:job_id>/apply-direct", methods=["POST", "GET"])
def apply_direct(job_id):
    store_data = load_json(JOBS_FILE, {"jobs": []})
    all_jobs = store_data.get("jobs", [])
    job = next((j for j in all_jobs if j.get("id") == job_id), None)

    if not job:
        return jsonify({"error": f"Job '{job_id}' not found"}), 404

    if request.method == "GET":
        # GET is read/redirect only - MUST NOT create application!
        return jsonify({
            "job_id": job_id,
            "company": job.get("company"),
            "title": job.get("title"),
            "application_url": job.get("url"),
            "notice": "GET is read/redirect only. Use POST to record application."
        }), 200

    target_url = job.get("url") or job.get("canonical_url")
    if not target_url or not str(target_url).startswith("http"):
        comp_id = (job.get("company") or "").lower().replace(" ", "-")
        companies = load_json(COMPANIES_FILE, [])
        comp_obj = next((c for c in companies if isinstance(c, dict) and (c.get("id") == comp_id or c.get("name", "").lower() == (job.get("company") or "").lower())), None)
        target_url = comp_obj.get("career_url") if comp_obj else None
    if not target_url or not str(target_url).startswith("http"):
        # No real application URL known for this job. Never substitute a
        # fake/placeholder URL (see product-context.md hard boundaries).
        return jsonify({
            "error": "No application URL available for this job",
            "job_id": job_id,
            "application_url": None
        }), 422
    mark_job_viewed(job_id)

    try:
        tracker = ApplicationTracker()
        app_record = tracker.create_application(
            job_id=job_id,
            company=job.get("company", "Tech Company"),
            job_title=job.get("title", "Software Developer"),
            location=job.get("location", "India"),
            application_url=target_url,
            match_score=_get_job_match_score(job, 50)
        )
    except Exception as e:
        app_record = {"status": "applied", "job_id": job_id}

    return jsonify({"status": "applied", "job_id": job_id, "application_url": target_url, "application": app_record}), 200

@app.route("/api/jobs/<path:job_id>/toggle-save", methods=["POST"])
def toggle_job_saved(job_id):
    saved_data = load_json(SAVED_JOBS_FILE, {"saved_jobs": []})
    saved_list = saved_data.get("saved_jobs", [])

    is_saved = False
    if job_id in saved_list:
        saved_list.remove(job_id)
    else:
        saved_list.append(job_id)
        is_saved = True

    saved_data["saved_jobs"] = saved_list
    save_json(SAVED_JOBS_FILE, saved_data)
    return jsonify({"status": "updated", "job_id": job_id, "is_saved": is_saved, "total_saved": len(saved_list)}), 200

@app.route("/api/jobs/<path:job_id>/toggle-apply-later", methods=["POST"])
def toggle_apply_later(job_id):
    """Tracker 'Apply Later' bucket - same storage pattern as saved jobs."""
    data = load_json(APPLY_LATER_FILE, {"apply_later": []})
    lst = data.get("apply_later", [])
    is_on = False
    if job_id in lst:
        lst.remove(job_id)
    else:
        lst.append(job_id)
        is_on = True
    data["apply_later"] = lst
    save_json(APPLY_LATER_FILE, data)
    return jsonify({"status": "updated", "job_id": job_id,
                    "apply_later": is_on, "total": len(lst)}), 200


@app.route("/api/jobs/apply-later", methods=["GET"])
def get_apply_later_jobs():
    data = load_json(APPLY_LATER_FILE, {"apply_later": []})
    lst = data.get("apply_later", [])
    store_data = load_json(JOBS_FILE, {"jobs": []})
    all_map = {j["id"]: j for j in store_data.get("jobs", []) if j.get("id")}
    jobs = [all_map[jid] for jid in lst if jid in all_map]
    return jsonify({"total": len(jobs), "jobs": jobs})


@app.route("/api/jobs/saved", methods=["GET"])
def get_saved_jobs():
    saved_data = load_json(SAVED_JOBS_FILE, {"saved_jobs": []})
    saved_list = saved_data.get("saved_jobs", [])

    curated_data = load_json(JOBS_CURATED_FILE, {"jobs": []})
    store_data = load_json(JOBS_FILE, {"jobs": []})
    all_jobs_map = {j["id"]: j for j in store_data.get("jobs", [])}
    for j in curated_data.get("jobs", []):
        all_jobs_map[j["id"]] = j

    result_jobs = []
    for jid in saved_list:
        job = all_jobs_map.get(jid)
        if job:
            result_jobs.append(job)
        else:
            result_jobs.append({
                "id": jid,
                "title": f"Saved Job ({jid})",
                "company": "Tech Partner",
                "location": "India",
                "url": "https://www.flipkartcareers.com/",
                "source": "saved_history"
            })

    return jsonify({"total_saved": len(result_jobs), "jobs": result_jobs})

@app.route("/api/jobs/search", methods=["POST"])
def real_time_search():
    body = request.get_json(force=True) or {}
    filters = body.get("filters") or body
    if not isinstance(filters, dict):
        filters = {}
    filters["is_manual_search"] = True

    status_resp = bg_worker.trigger_interactive_search(custom_filters=filters)
    http_code = 409 if status_resp.get("status") == "running" else 202
    return jsonify(status_resp), http_code

@app.route("/api/jobs/search/status/<task_id>", methods=["GET"])
def get_search_task_status(task_id):
    st = bg_worker.get_interactive_search_status(task_id)
    if st.get("status") == "not_found":
        return jsonify(st), 404
    return jsonify(st), 200

@app.route("/api/system-pulse", methods=["GET"])
def system_pulse():
    """Small live snapshot for the Insights tab: LLM quota + scan activity."""
    cfg = load_json(os.path.join(BASE_DIR, "config.json"), {})
    keys = cfg.get("llm", {}).get("keys", [])
    prov = {}
    for k in keys:
        p = k.get("provider")
        if not p:
            continue
        d = prov.setdefault(p, {"used_today": 0, "daily_limit": 0})
        d["used_today"] += int(k.get("used_today", 0) or 0)
        d["daily_limit"] += int(k.get("daily_limit", 0) or 0)
    status = bg_worker.get_status()
    return jsonify({
        "llm_providers": prov,
        "background_search": {
            "enabled": status.get("enabled"),
            "interval_hours": status.get("interval_hours"),
            "last_search": status.get("last_search_time"),
            "next_search": status.get("next_search_time"),
            "is_running": status.get("is_running"),
        },
    })


@app.route("/api/company-health", methods=["GET"])
def company_health():
    """Transparency: how much of the company list is actually hiring freshers,
    plus what the autonomous discovery worker has been doing."""
    comps = load_json(COMPANIES_FILE, {"companies": []}).get("companies", [])
    metrics = load_json(os.path.join(BASE_DIR, "company_metrics.json"), {}).get("companies", {})

    total = len(comps)
    scanned = fresher_active = producing = 0
    for c in comps:
        m = metrics.get(c.get("id")) or {}
        if m.get("total_scans", 0) > 0:
            scanned += 1
        if m.get("jobs_extracted", 0) > 0:
            producing += 1
        if m.get("fresher_jobs_total", 0) > 0 and m.get("fresher_zero_streak", 99) <= 3:
            fresher_active += 1

    disc = load_json(os.path.join(BASE_DIR, "company_discovery_log.json"), {"cycles": []})
    cycles = disc.get("cycles", [])[-5:]
    watchlist = load_json(os.path.join(BASE_DIR, "company_watchlist.json"), {"companies": {}})

    # --- progress toward the 75% fresher-active target ---
    # Adding x fresher-active companies moves (fa + x) / (total + x); solving
    # for 75%: x = (0.75 * total - fa) / 0.25. The ETA uses the measured
    # verified-additions rate from the discovery log's trailing 14 days
    # (every admission is fresher-active on day one by the gate), so the
    # number is honest, not aspirational.
    TARGET = 0.75
    needed = max(0, int((TARGET * total - fresher_active) / (1 - TARGET)) + 1) \
        if total and fresher_active / max(total, 1) < TARGET else 0
    cutoff_dt = datetime.now(timezone.utc).timestamp() - 14 * 86400
    recent_adds = 0
    for cyc in disc.get("cycles", []):
        try:
            ts = datetime.fromisoformat(cyc.get("finished_at", "").replace("Z", "+00:00")).timestamp()
            if ts >= cutoff_dt:
                recent_adds += len(cyc.get("added", []) or [])
        except Exception:
            continue
    rate_per_day = recent_adds / 14.0
    eta_days = int(needed / rate_per_day) if rate_per_day > 0 and needed else None

    return jsonify({
        "target_pct": 75,
        "companies_needed_for_target": needed,
        "recent_adds_14d": recent_adds,
        "eta_days_to_target": eta_days,
        "total_companies": total,
        "scanned_at_least_once": scanned,
        "producing_jobs": producing,
        "fresher_active": fresher_active,
        "fresher_active_pct": round(100.0 * fresher_active / total, 1) if total else 0.0,
        "target_pct": 75.0,
        "watchlist_size": len(watchlist.get("companies", {})),
        "discovery_recent_cycles": [
            {"finished_at": c.get("finished_at"), "category": c.get("category"),
             "probed": c.get("candidates_probed"), "verified": c.get("verified"),
             "added": c.get("added"), "rejections": c.get("rejections")}
            for c in cycles
        ],
    }), 200


@app.route("/api/ollama-status", methods=["GET"])
def get_ollama_status():
    cfg = load_json(CONFIG_FILE, {}).get("ollama", {})
    base_url = cfg.get("base_url", "http://localhost:11434").rstrip("/")
    target_model = cfg.get("model", "qwen2.5:7b")

    is_available = False
    try:
        import requests
        r = requests.get(f"{base_url}/api/tags", timeout=3)
        if r.status_code == 200:
            is_available = True
    except Exception:
        is_available = False

    scoring_logs = load_json(os.path.join(BASE_DIR, "scoring_log.json"), {"logs": []}).get("logs", [])
    today_str = datetime.now().strftime("%Y-%m-%d")

    tier4_logs_today = []
    for log in scoring_logs:
        if log.get("tier") == 4 or log.get("llm_used") == "ollama_local_qwen2.5":
            ts = log.get("timestamp", "")
            if ts.startswith(today_str):
                tier4_logs_today.append(log)

    jobs_today_count = len(tier4_logs_today)
    response_times = [l.get("response_time_seconds") for l in tier4_logs_today if l.get("response_time_seconds") is not None]
    avg_resp_time = round(sum(response_times) / len(response_times), 2) if response_times else 0.0

    return jsonify({
        "available": is_available,
        "model": target_model,
        "jobs_scored_today": jobs_today_count,
        "avg_response_time_seconds": avg_resp_time
    })

@app.route("/api/concurrency-status", methods=["GET"])
def get_concurrency_status():
    from adaptive_concurrency_manager import AdaptiveConcurrencyManager
    mgr = AdaptiveConcurrencyManager()
    current_ram = mgr.get_current_ram_percent()

    logs = load_json(os.path.join(BASE_DIR, "concurrency_log.json"), {"batches": []}).get("batches", [])
    last_batch = logs[-1] if logs else {}

    return jsonify({
        "active_concurrency": last_batch.get("concurrent_browsers", mgr.current_concurrency),
        "min_concurrent": mgr.min_concurrent,
        "max_concurrent": mgr.max_concurrent,
        "ram_percent": current_ram,
        "last_adjustment": last_batch.get("adjustment", "Holding steady"),
        "total_batches_logged": len(logs)
    })

@app.route("/api/consensus-stats", methods=["GET"])
def get_consensus_stats():
    logs = load_json(os.path.join(BASE_DIR, "consensus_log.json"), {"verifications": []}).get("verifications", [])
    today_str = datetime.now().strftime("%Y-%m-%d")

    today_logs = [l for l in logs if l.get("timestamp", "").startswith(today_str)]
    total_verified = len(logs)
    verified_today = len(today_logs)

    agree_count = sum(1 for l in logs if l.get("consensus"))
    disputed_count = sum(1 for l in logs if l.get("flag") == "scores_disagree")
    agreement_rate = round((agree_count / total_verified) * 100, 1) if total_verified > 0 else 100.0

    return jsonify({
        "total_verified": total_verified,
        "verified_today": verified_today,
        "agreement_rate_percent": agreement_rate,
        "disputed_count": disputed_count
    })

@app.route("/api/pattern-health", methods=["GET"])
def get_pattern_health():
    from pattern_store import PatternStore
    p_store = PatternStore()
    patterns = p_store.patterns

    total_patterns = len(patterns)
    stale_count = 0
    active_count = 0

    for comp_id, info in patterns.items():
        pat = info.get("last_successful_pattern", {})
        if pat.get("status") == "stale" or pat.get("needs_relearning"):
            stale_count += 1
        else:
            active_count += 1

    reval_logs = load_json(os.path.join(BASE_DIR, "pattern_revalidation_log.json"), {"revalidations": []}).get("revalidations", [])
    last_reval = reval_logs[-1].get("timestamp") if reval_logs else None

    return jsonify({
        "total_patterns": total_patterns,
        "active_patterns": active_count,
        "stale_patterns": stale_count,
        "last_revalidation": last_reval
    })

@app.route("/api/background-search/status", methods=["GET"])
def get_background_search_status():
    status = bg_worker.get_status()
    cfg = load_json(CONFIG_FILE, {}).get("background_search", {})
    status["hierarchical_filters"] = cfg.get("hierarchical_filters", {})
    return jsonify(status)

@app.route("/api/background-search/config", methods=["GET", "POST"])
def manage_background_search_config():
    cfg = load_json(CONFIG_FILE, {})
    if request.method == "POST":
        body = request.get_json(force=True) or {}
        bg_cfg = cfg.get("background_search", {})
        
        if "enabled" in body:
            bg_cfg["enabled"] = bool(body["enabled"])
        if "interval_hours" in body:
            bg_cfg["interval_hours"] = int(body["interval_hours"])
        if "default_filters" in body:
            bg_cfg["default_filters"] = body["default_filters"]
        if "global_filters" in body:
            bg_cfg["global_filters"] = body["global_filters"]
            if "hierarchical_filters" in bg_cfg and isinstance(bg_cfg["hierarchical_filters"], dict):
                bg_cfg["hierarchical_filters"]["global_filters"] = body["global_filters"]
        if "hierarchical_filters" in body:
            bg_cfg["hierarchical_filters"] = body["hierarchical_filters"]
            if isinstance(body["hierarchical_filters"], dict) and "global_filters" in body["hierarchical_filters"]:
                bg_cfg["global_filters"] = body["hierarchical_filters"]["global_filters"]

        cfg["background_search"] = bg_cfg
        save_json(CONFIG_FILE, cfg)
        bg_worker.update_config(bg_cfg)
        return jsonify({"message": "Auto-Scout configuration updated", "config": bg_cfg}), 200
    else:
        bg_cfg = cfg.get("background_search", {
            "enabled": True,
            "interval_hours": 2,
            "default_filters": {"min_match_score": 55},
            "global_filters": {},
            "hierarchical_filters": {}
        })
        return jsonify(bg_cfg), 200

@app.route("/api/background-search/log", methods=["GET"])
def get_background_search_log():
    limit = int(request.args.get("limit", 20))
    return jsonify({"cycles": bg_worker.get_logs(limit=limit)})

@app.route("/api/metrics", methods=["GET"])
def get_metrics():
    data = load_json(METRICS_FILE, {"companies": {}})
    return jsonify(data)

@app.route("/api/scan-order", methods=["GET"])
def get_scan_order():
    _, scan_order_data = rank_companies()
    return jsonify(scan_order_data)

@app.route("/api/company/add", methods=["POST"])
def add_company():
    body = request.get_json(force=True)
    if not body or "name" not in body or "career_url" not in body:
        return jsonify({"error": "Missing 'name' or 'career_url'"}), 400

    cid = body.get("id") or body["name"].lower().replace(" ", "-")
    new_comp = {
        "id": cid,
        "name": body["name"],
        "career_url": body["career_url"],
        "ats": body.get("ats", "custom"),
        "difficulty_estimate": body.get("difficulty_estimate", 0.8),
        "your_preference_score": body.get("your_preference_score", 0.70),
        "success_rate": 0,
        "avg_salary_inr": body.get("avg_salary_inr", 0),
        "parsed_count": 0,
        "parsing_accuracy": 0,
        "last_parsed": None
    }

    data = load_json(COMPANIES_FILE, {"companies": []})
    companies = data.get("companies", [])
    
    if any(c["id"] == cid for c in companies):
        return jsonify({"error": f"Company with id '{cid}' already exists."}), 400

    companies.append(new_comp)
    data["companies"] = companies
    save_json(COMPANIES_FILE, data)
    
    rank_companies()
    return jsonify({"message": "Company added successfully", "company": new_comp}), 201

@app.route("/api/company/<company_id>/rescan", methods=["POST"])
def rescan_company(company_id):
    companies_data = load_json(COMPANIES_FILE, {"companies": []})
    if not any(c["id"] == company_id for c in companies_data.get("companies", [])):
        return jsonify({"error": f"Company '{company_id}' not found"}), 404

    def run_rescan():
        coordinator = ScanCoordinator()
        coordinator.run_scan(target_company_id=company_id)

    thread = threading.Thread(target=run_rescan, daemon=True)
    thread.start()

    return jsonify({"message": f"Rescan initiated for company '{company_id}'"}), 202

def _app_startup_safeguard():
    try:
        _raw_jobs = load_json(JOBS_FILE, {"jobs": []}).get("jobs", [])
        _sdata = {"jobs": []}
        _changed = False
        for _j in _raw_jobs:
            if not isinstance(_j.get("match"), dict):
                _j["match"] = None
                _changed = True
            _sdata["jobs"].append(_j)
        if _changed:
            save_json(JOBS_FILE, _sdata)
        else:
            print(f"[AppStartup] Store Integrity Safeguard verified: {len(_raw_jobs)} real jobs.")
    except Exception as _e:
        print(f"[AppStartup] Safeguard check error: {_e}")


def _get_job_match_score(job, default=50):
    if not isinstance(job, dict):
        return default
    match_obj = job.get("match")
    if not isinstance(match_obj, dict):
        return default
    score_val = match_obj.get("score")
    if score_val is None or not isinstance(score_val, (int, float)):
        return default
    return int(score_val)

rescore_lock = threading.Lock()
active_rescore_hash = None
pending_rescore_data = None

def _async_rescore_jobs(resume_data):
    global active_rescore_hash, pending_rescore_data
    if not resume_data or not isinstance(resume_data, dict):
        return

    version_hash = resume_data.get("version_hash")
    active_rescore_hash = version_hash

    if not rescore_lock.acquire(blocking=False):
        print(f"[AppRescore] Rescoring already in progress. Queueing pending rescore for {version_hash}.")
        pending_rescore_data = resume_data
        return

    now_iso = datetime.now().isoformat()
    r_store = load_json(RESUME_FILE, resume_data or {})
    rescore_status = {
        "status": "in_progress",
        "total_jobs": 0,
        "scored_jobs": 0,
        "failed_jobs": 0,
        "started_at": now_iso,
        "completed_at": None,
        "error": None,
        "resume_version_hash": version_hash
    }
    r_store["rescore_status"] = rescore_status
    save_json(RESUME_FILE, r_store)

    try:
        scorer = HybridJobScorer(resume_data)
        jobs_data = load_json(JOBS_FILE, {"jobs": []})
        jobs_list = jobs_data.get("jobs", [])
        total_jobs = len(jobs_list)
        rescore_status["total_jobs"] = total_jobs
        r_store["rescore_status"] = rescore_status
        save_json(RESUME_FILE, r_store)

        scored_count = 0
        failed_count = 0
        for idx, job in enumerate(jobs_list):
            if active_rescore_hash != version_hash:
                print(f"[AppRescore] Obsolete rescore thread for hash {version_hash} aborted in favor of {active_rescore_hash}.")
                return

            try:
                job["match"] = scorer.score_job(job)
                scored_count += 1
            except Exception as item_err:
                print(f"[AppRescore] Error scoring job {job.get('id')}: {item_err}")
                failed_count += 1

            if (idx + 1) % 25 == 0 or (idx + 1) == total_jobs:
                rescore_status["scored_jobs"] = scored_count
                rescore_status["failed_jobs"] = failed_count
                r_store["rescore_status"] = rescore_status
                save_json(RESUME_FILE, r_store)

        if active_rescore_hash != version_hash:
            print(f"[AppRescore] Obsolete rescore thread for hash {version_hash} aborted before final write.")
            return

        # QUALITY REFINEMENT PASS (user's quality-first directive): the cheap
        # pass above filters the pile; feed CANDIDATES (local score >= refine
        # threshold) are rescored through the paid LLM tier for real semantic
        # matching + reasoning.
        #
        # HARD CAP, added 2026-08-29 after measuring real free-tier capacity:
        # Gemini free keys allow only ~50 calls/key/day (9 of 10 keys returned
        # 429 after 23-52 calls), and Groq rate-limits at the ORGANISATION
        # level on tokens, not per key. Actual ceiling is a few hundred LLM
        # scores per day - not the 14,400 the config used to claim. Refining
        # all ~2,900 candidates would take a week and starve the top of the
        # feed, which is the only part actually read. So: refine the BEST
        # candidates first, capped per pass, and let later passes walk down
        # the list as quota frees up.
        refine_threshold = 50
        refine_cap = int(os.environ.get("REFINE_CAP", "150"))
        candidates = [j for j in jobs_list
                      if isinstance(j.get("match"), dict)
                      and j["match"].get("tier") not in (1, 2)
                      and (j["match"].get("score") or 0) >= refine_threshold]
        candidates.sort(key=lambda j: -(j["match"].get("score") or 0))
        total_candidates = len(candidates)
        candidates = candidates[:refine_cap]
        refined = 0
        if candidates:
            print(f"[AppRescore] Quality refinement: top {len(candidates)} of "
                  f"{total_candidates} feed candidates -> paid LLM tier "
                  f"(free-tier daily ceiling)...")

            def _persist_refined(batch):
                # merge refined paid-tier scores into the live store now, so
                # progress survives restarts and the feed improves as we go
                by_id = {j.get("id"): j.get("match") for j in batch if j.get("id")}
                fd = load_json(JOBS_FILE, {"jobs": []})
                for fj in fd.get("jobs", []):
                    m = by_id.get(fj.get("id"))
                    if isinstance(m, dict) and m.get("tier") in (1, 2):
                        fj["match"] = m
                save_json(JOBS_FILE, fd)

            pending_persist = []
            for job in candidates:
                if active_rescore_hash != version_hash:
                    _persist_refined(pending_persist)
                    return
                try:
                    new_match = scorer.score_job(job, force_tier="paid_llm")
                    if isinstance(new_match, dict):
                        job["match"] = new_match
                        refined += 1
                        if new_match.get("tier") in (1, 2):
                            pending_persist.append(job)
                except Exception as ref_err:
                    print(f"[AppRescore] Refinement error on {job.get('id')}: {ref_err}")
                time.sleep(0.5)  # pace: stay within the key pool's aggregate RPM
                if len(pending_persist) >= 25:
                    _persist_refined(pending_persist)
                    pending_persist = []
                    print(f"[AppRescore] Refinement progress: {refined}/{len(candidates)}")
            _persist_refined(pending_persist)
            print(f"[AppRescore] Quality refinement complete: {refined}/{len(candidates)} rescored via LLM.")

        # Merge scores into a FRESHLY loaded store rather than overwriting it
        # with our stale in-memory copy: a scan may have added/updated jobs
        # while we were scoring (observed live: a running scan and this
        # rescorer clobbering each other's writes, losing all 568 scores).
        scored_by_id = {j.get("id"): j.get("match") for j in jobs_list if j.get("id")}
        fresh_data = load_json(JOBS_FILE, {"jobs": []})
        fresh_jobs = fresh_data.get("jobs", [])
        for fj in fresh_jobs:
            fid = fj.get("id")
            ours = scored_by_id.get(fid)
            if not isinstance(ours, dict):
                continue
            theirs = fj.get("match")
            # take our score when the store has none, or when ours came from
            # a better (paid LLM) tier than what the store holds
            ours_paid = ours.get("tier") in (1, 2)
            theirs_paid = isinstance(theirs, dict) and theirs.get("tier") in (1, 2)
            if not isinstance(theirs, dict) or (ours_paid and not theirs_paid):
                fj["match"] = ours
        fresh_data["jobs"] = fresh_jobs
        save_json(JOBS_FILE, fresh_data)

        rescore_status["status"] = "completed"
        rescore_status["scored_jobs"] = scored_count
        rescore_status["failed_jobs"] = failed_count
        rescore_status["completed_at"] = datetime.now().isoformat()
        r_store["rescore_status"] = rescore_status
        save_json(RESUME_FILE, r_store)

        print(f"[AppRescore] Rescored {scored_count}/{total_jobs} jobs in background successfully for hash {version_hash}.")
    except Exception as e:
        print(f"[AppRescore] Error rescoring jobs in background: {e}")
        rescore_status["status"] = "error"
        rescore_status["error"] = str(e)
        rescore_status["completed_at"] = datetime.now().isoformat()
        r_store["rescore_status"] = rescore_status
        save_json(RESUME_FILE, r_store)
    finally:
        rescore_lock.release()
        if pending_rescore_data and pending_rescore_data.get("version_hash") == active_rescore_hash:
            next_data = pending_rescore_data
            pending_rescore_data = None
            threading.Thread(target=_async_rescore_jobs, args=(next_data,), daemon=True).start()

@app.route("/api/resume", methods=["POST"])
def upload_resume():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    parsed = parse_resume(filepath)
    raw_text = parsed["raw_text"]
    skills = parsed["skills"]
    exp = parsed["estimated_years_experience"]

    v_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]
    now_iso = datetime.now().isoformat()

    chunker = ChunkingService(chunk_size=600, overlap=100)
    chunks = chunker.chunk_text(raw_text)

    embedder = EmbeddingService()
    vector_store = VectorStoreService()
    vector_store.clear()

    embeddings = []
    metadata_list = []
    for c in chunks:
        emb = embedder.get_embedding(c["content"])
        if emb is not None:
            embeddings.append(emb)
            metadata_list.append({
                "chunk_id": c["chunk_id"],
                "content": c["content"],
                "version_hash": v_hash
            })

    if embeddings:
        vector_store.add_embeddings(embeddings, metadata_list)


    resume_data = {
        "has_resume": True,
        "skills": skills,
        "estimated_years_experience": exp,
        "chunk_count": len(chunks),
        "uploaded_at": now_iso,
        "version_hash": v_hash,
        "raw_text": raw_text
    }
    save_json(RESUME_FILE, resume_data)

    threading.Thread(target=_async_rescore_jobs, args=(resume_data,), daemon=True).start()

    return jsonify({
        "has_resume": True,
        "skills": skills,
        "estimated_years_experience": exp,
        "chunk_count": len(chunks),
        "uploaded_at": now_iso
    }), 200

@app.route("/api/resume", methods=["GET"])
def get_resume():
    data = load_json(RESUME_FILE, {"has_resume": False, "skills": [], "chunk_count": 0})
    return jsonify({
        "has_resume": data.get("has_resume", False),
        "skills": data.get("skills", []),
        "chunk_count": data.get("chunk_count", 0),
        "uploaded_at": data.get("uploaded_at")
    })

@app.route("/api/resume-status", methods=["GET"])
def get_resume_status():
    data = load_json(RESUME_FILE, {"has_resume": False, "skills": [], "chunk_count": 0})
    jobs_data = load_json(JOBS_FILE, {"jobs": []})
    jobs_list = jobs_data.get("jobs", [])
    scored_jobs_count = sum(1 for j in jobs_list if j.get("match"))

    r_status = data.get("rescore_status") or {
        "status": "idle" if data.get("has_resume") else "no_resume",
        "total_jobs": len(jobs_list),
        "scored_jobs": scored_jobs_count,
        "failed_jobs": 0,
        "started_at": None,
        "completed_at": None,
        "error": None,
        "resume_version_hash": data.get("version_hash")
    }

    return jsonify({
        "has_resume": data.get("has_resume", False),
        "skills_count": len(data.get("skills", [])),
        "chunk_count": data.get("chunk_count", 0),
        "uploaded_at": data.get("uploaded_at"),
        "total_jobs": len(jobs_list),
        "scored_jobs": r_status.get("scored_jobs", scored_jobs_count),
        "version_hash": data.get("version_hash"),
        "rescore_status": r_status,
        "status": r_status.get("status", "idle"),
        "failed_jobs": r_status.get("failed_jobs", 0),
        "started_at": r_status.get("started_at"),
        "completed_at": r_status.get("completed_at"),
        "error": r_status.get("error")
    })

@app.route("/api/llm-quota", methods=["GET"])
def get_llm_quota():
    router = LLMRouter()
    return jsonify(router.get_quota_status())

@app.route("/api/rescore-all", methods=["POST"])
def rescore_all_jobs():
    resume_data = load_json(RESUME_FILE, {})
    if not resume_data.get("has_resume"):
        return jsonify({"error": "No resume uploaded yet"}), 400

    scorer = HybridJobScorer(resume_data)
    jobs_data = load_json(JOBS_FILE, {"jobs": []})
    jobs_list = jobs_data.get("jobs", [])
    
    for job in jobs_list:
        job["match"] = None
        job["match"] = scorer.score_job(job)

    jobs_data["jobs"] = jobs_list
    save_json(JOBS_FILE, jobs_data)
    return jsonify({"message": f"Successfully rescored {len(jobs_list)} jobs"}), 200

# --- PHASE 3 FEEDBACK ENDPOINTS ---

@app.route("/api/job/<job_id>/feedback", methods=["POST"])
def record_job_feedback(job_id):
    body = request.get_json(force=True) or {}
    action = body.get("action")
    reason = body.get("reason", "")

    if not action or action.lower() not in ["yes", "no"]:
        return jsonify({"error": "Action must be 'yes' or 'no'"}), 400

    jobs_data = load_json(JOBS_FILE, {"jobs": []})
    target_job = next((j for j in jobs_data.get("jobs", []) if j.get("id") == job_id), {})

    collector = FeedbackCollector()
    entry = collector.record_feedback(job_id, action, reason, target_job)

    job_title = target_job.get("title", "")
    learned_entry = None
    if action.lower() == "yes":
        learned_entry = learn_from_positive_feedback(job_title, reason)
    else:
        learned_entry = learn_from_negative_feedback(job_title, reason)

    adjustments = analyze_and_optimize()
    rec_engine = RecommendationEngine()
    recs = rec_engine.generate_recommendations()

    return jsonify({
        "status": "recorded",
        "feedback_entry": entry,
        "threshold_adjustments": len(adjustments),
        "learned_keyword": learned_entry is not None,
        "recommendations": recs
    }), 200

@app.route("/api/filter-metrics", methods=["GET"])
def get_filter_metrics():
    data = load_json(FILTER_METRICS_FILE, {})
    return jsonify(data)

@app.route("/api/feedback-summary", methods=["GET"])
def get_feedback_summary():
    collector = FeedbackCollector()
    return jsonify(collector.aggregate_feedback())

@app.route("/api/auto-improvement-log", methods=["GET"])
def get_auto_improvement_log():
    data = load_json(AUTO_LOG_FILE, {"improvements": []})
    raw_items = data.get("improvements", [])
    trials_data = load_json(os.path.join(BASE_DIR, "trial_periods.json"), {"trials": []})
    trials_map = {t.get("adjustment_id"): t for t in trials_data.get("trials", []) if t.get("adjustment_id")}

    deduped = []
    seen = []
    for item in raw_items:
        ts_str = item.get("timestamp", "")
        key = (item.get("type"), item.get("filter"), item.get("reason"), item.get("new_keyword"))
        is_dup = False
        if ts_str:
            try:
                item_dt = datetime.fromisoformat(ts_str)
                for (prev_key, prev_dt) in seen:
                    if prev_key == key and abs((item_dt - prev_dt).total_seconds()) < 60:
                        is_dup = True
                        break
                if not is_dup:
                    seen.append((key, item_dt))
            except Exception:
                pass
        if not is_dup:
            item_copy = dict(item)
            adj_id = item_copy.get("adjustment_id")
            if adj_id and adj_id in trials_map:
                t_status = trials_map[adj_id].get("status")
                if t_status == "pending":
                    item_copy["display_status"] = "Pending trial"
                elif t_status == "confirmed":
                    item_copy["display_status"] = "Confirmed effective"
                elif t_status == "reverted":
                    item_copy["display_status"] = "Reverted - did not help"
            elif item_copy.get("type") == "threshold_reversion":
                item_copy["display_status"] = "Reverted - did not help"
            elif item_copy.get("type") == "threshold_confirmation":
                item_copy["display_status"] = "Confirmed effective"
            elif item_copy.get("type") == "threshold_adjustment":
                item_copy["display_status"] = "Pending trial"
            else:
                item_copy["display_status"] = "Active"

            deduped.append(item_copy)
    return jsonify({"improvements": deduped})

@app.route("/api/apify-status", methods=["GET"])
def get_apify_status():
    aggregator = InsightsAggregator()
    return jsonify(aggregator.get_apify_fallback_status())

@app.route("/api/trial-periods", methods=["GET"])
def get_trial_periods():
    trials_data = load_json(os.path.join(BASE_DIR, "trial_periods.json"), {"trials": []})
    return jsonify(trials_data)

@app.route("/api/keyword-history", methods=["GET"])
def get_keyword_history():
    filters = load_json(FILTERS_FILE, {})
    auto_log = load_json(AUTO_LOG_FILE, {"improvements": []})
    keyword_logs = [item for item in auto_log.get("improvements", []) if "keyword" in item.get("type", "")]

    return jsonify({
        "current_filters": filters,
        "learned_history": keyword_logs
    })

@app.route("/api/filters", methods=["GET", "POST"])
def manage_filters():
    if request.method == "POST":
        body = request.get_json(force=True) or {}
        save_json(FILTERS_FILE, body)
        return jsonify({"message": "Filters updated successfully", "filters": body}), 200
    else:
        filters = load_json(FILTERS_FILE, {})
        return jsonify(filters), 200

@app.route("/api/reset-filters", methods=["POST"])
def reset_filters_endpoint():
    default_filters = {
        "target_role": ["Software Engineer", "Intern", "Fresher", "Entry-level", "Backend Engineer", "Frontend Engineer", "Full Stack Engineer", "Microsoft Developer"],
        "target_location": ["Gurugram", "Bangalore", "Delhi", "Noida", "Hyderabad", "Mumbai", "Remote", "India"],
        "target_experience": ["0 years", "Fresher", "Entry-level", "0-1 years", "0-2 years"],
        "exclude_keywords": ["Senior", "Lead", "Manager", "Principal", "Director", "Architect", "Staff"]
    }
    save_json(FILTERS_FILE, default_filters)
    return jsonify({"message": "Filters reset to defaults", "filters": default_filters}), 200

@app.route("/api/clear-cache", methods=["POST"])
def clear_cache_endpoint():
    save_json(JOBS_CURATED_FILE, {"last_search": None, "jobs": []})
    return jsonify({"message": "Search cache cleared successfully"}), 200

# --- PHASE 4 APPLICATION TRACKER & ANALYTICS ENDPOINTS ---

@app.route("/api/applications", methods=["POST"])
def create_application():
    body = request.get_json(force=True) or {}
    job_id = body.get("job_id")
    company = body.get("company")
    job_title = body.get("job_title")
    location = body.get("location", "India")

    if not job_id or not company or not job_title:
        return jsonify({"error": "Missing 'job_id', 'company', or 'job_title'"}), 400

    tracker = ApplicationTracker()
    app_record = tracker.create_application(
        job_id=job_id,
        company=company,
        job_title=job_title,
        location=location,
        applied_date=body.get("applied_date"),
        application_url=body.get("application_url"),
        match_score=body.get("match_score", 50)
    )

    return jsonify(app_record), 201

@app.route("/api/applications", methods=["GET"])
def list_applications():
    st = request.args.get("status")
    comp = request.args.get("company")
    loc = request.args.get("location")

    tracker = ApplicationTracker()
    apps = tracker.list_applications(status=st, company=comp, location=loc)
    return jsonify({"applications": apps, "total": len(apps)})

@app.route("/api/applications/<app_id>", methods=["GET"])
def get_single_application(app_id):
    tracker = ApplicationTracker()
    app_rec = tracker.get_application(app_id)
    if not app_rec:
        return jsonify({"error": f"Application '{app_id}' not found"}), 404
    return jsonify(app_rec)

@app.route("/api/applications/<app_id>", methods=["PATCH"])
def update_application(app_id):
    body = request.get_json(force=True) or {}
    new_status = body.get("status")
    note = body.get("notes") or body.get("note")
    tags = body.get("tags")
    salary_inr = body.get("salary_offered_inr") or body.get("salary_inr")
    rejection_reason = body.get("rejection_reason")
    referral_received = body.get("referral_received")
    was_shortlisted = body.get("was_shortlisted")
    interview_date = body.get("interview_date")

    tracker = ApplicationTracker()
    try:
        updated = tracker.update_status(
            app_id=app_id,
            new_status=new_status,
            note=note,
            tags=tags,
            salary_inr=salary_inr,
            rejection_reason=rejection_reason,
            referral_received=referral_received,
            was_shortlisted=was_shortlisted,
            interview_date=interview_date
        )
        if not updated:
            return jsonify({"error": f"Application '{app_id}' not found"}), 404
        return jsonify(updated), 200
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400

@app.route("/api/applications/<app_id>", methods=["DELETE"])
def delete_application(app_id):
    tracker = ApplicationTracker()
    archived = tracker.archive_application(app_id)
    if not archived:
        return jsonify({"error": f"Application '{app_id}' not found"}), 404
    return jsonify({"id": app_id, "status": "archived", "message": "Application archived successfully"}), 200



# --- PHASE 4 ANALYSIS & RECOMMENDATION ENDPOINTS ---

@app.route("/api/analysis/company/<company_name>", methods=["GET"])
def analyze_company_endpoint(company_name):
    analyzer = CompanyAnalyzer()
    return jsonify(analyzer.analyze_company(company_name))

@app.route("/api/analysis/role/<path:role_title>", methods=["GET"])
def analyze_role_endpoint(role_title):
    analyzer = RoleAnalyzer()
    return jsonify(analyzer.analyze_role(role_title))

@app.route("/api/analysis/location/<location>", methods=["GET"])
def analyze_location_endpoint(location):
    analyzer = LocationAnalyzer()
    return jsonify(analyzer.analyze_location(location))

@app.route("/api/analysis/timeline", methods=["GET"])
def analyze_timeline_endpoint():
    analyzer = TimelineAnalyzer()
    return jsonify(analyzer.analyze_timeline())

@app.route("/api/insights", methods=["GET"])
def get_insights_endpoint():
    generator = InsightGenerator()
    return jsonify({"insights": generator.generate_insights()})

@app.route("/api/recommendations", methods=["GET"])
def get_recommendations_endpoint():
    engine = RecommendationEngine()
    return jsonify({"recommendations": engine.generate_recommendations()})

@app.route("/api/dashboard-stats", methods=["GET"])
def get_dashboard_stats_endpoint():
    stats_engine = DashboardStats()
    return jsonify(stats_engine.get_summary_stats())

@app.route("/api/insights/efficiency-score", methods=["GET"])
def get_efficiency_score():
    aggregator = InsightsAggregator()
    eff_data = aggregator.compute_efficiency_score()
    wow_data = aggregator.compute_week_over_week_applications()
    skill_data = aggregator.compute_trending_skills()
    return jsonify({
        "efficiency": eff_data,
        "applications_wow": wow_data,
        "trending_skills": skill_data
    })

@app.route("/api/insights/worst-case-accuracy", methods=["GET"])
def get_worst_case_accuracy_endpoint():
    aggregator = InsightsAggregator()
    return jsonify(aggregator.compute_worst_case_accuracy())

@app.route("/api/personalization-stats", methods=["GET"])
def get_personalization_stats_endpoint():
    from feedback_example_selector import FeedbackExampleSelector
    selector = FeedbackExampleSelector()
    return jsonify(selector.get_personalization_stats())

@app.route("/api/insights/resource-allocation", methods=["GET"])
def get_resource_allocation_endpoint():
    aggregator = InsightsAggregator()
    return jsonify(aggregator.get_resource_allocation_status())

@app.route("/api/insights/cycle-yield-history", methods=["GET"])
def get_cycle_yield_history_endpoint():
    aggregator = InsightsAggregator()
    return jsonify(aggregator.get_cycle_yield_history())


@app.route("/api/background-search/test-config", methods=["POST"])
def test_background_search_config():
    body = request.get_json(force=True) or {}
    min_score = body.get("min_match_score") or body.get("min_score") or 55

    jobs_data = load_json(JOBS_FILE, {"jobs": []})
    curated_data = load_json(JOBS_CURATED_FILE, {"jobs": []})
    all_jobs = jobs_data.get("jobs", []) + curated_data.get("jobs", [])

    unique_jobs = {}
    for j in all_jobs:
        if j.get("id"):
            unique_jobs[j["id"]] = j

    eval_jobs = list(unique_jobs.values())
    matched_jobs = []

    for job in eval_jobs:
        score = _get_job_match_score(job, 50)
        if score >= min_score:
            matched_jobs.append(job)

    return jsonify({
        "would_match_count": len(matched_jobs),
        "total_evaluated": len(eval_jobs),
        "min_match_score": min_score,
        "sample_matches": [j.get("title") for j in matched_jobs[:3]]
    })

@app.route("/api/company/<company_id>/toggle-monitoring", methods=["POST"])
def toggle_company_monitoring(company_id):
    companies_data = load_json(COMPANIES_FILE, {"companies": []})
    comps = companies_data.get("companies", [])
    target = next((c for c in comps if c.get("id") == company_id), None)
    if not target:
        return jsonify({"error": f"Company '{company_id}' not found"}), 404

    curr_status = target.get("monitoring_status", "Active Monitoring")
    new_status = "Paused" if curr_status == "Active Monitoring" else "Active Monitoring"
    target["monitoring_status"] = new_status
    save_json(COMPANIES_FILE, companies_data)

    return jsonify({
        "id": company_id,
        "name": target.get("name"),
        "monitoring_status": new_status
    })

@app.route("/api/scan-status/live", methods=["GET"])
def get_live_scan_status_endpoint():
    return jsonify(bg_worker.get_live_scan_status())

@app.route("/api/background-search/reset", methods=["POST"])
def reset_background_search():
    bg_log_file = os.path.join(BASE_DIR, "background_search_log.json")
    save_json(bg_log_file, {"cycles": []})
    
    cfg = load_json(CONFIG_FILE, {})
    cfg["background_search"] = {
        "enabled": True,
        "interval_hours": 2,
        "default_filters": {
            "min_match_score": 55
        }
    }
    save_json(CONFIG_FILE, cfg)
    bg_worker.config = bg_worker._load_config()
    return jsonify({"message": "Background search history and settings reset to default"}), 200

@app.route("/api/export-data", methods=["GET"])
def export_data_endpoint():
    feedback_file = os.path.join(BASE_DIR, "feedback_log.json")
    app_tracker_file = os.path.join(BASE_DIR, "applications.json")
    
    resume_store = load_json(RESUME_FILE, {})
    resume_meta = {
        "has_resume": resume_store.get("has_resume", False),
        "skills": resume_store.get("skills", []),
        "chunk_count": resume_store.get("chunk_count", 0),
        "uploaded_at": resume_store.get("uploaded_at")
    }

    export_payload = {
        "exported_at": datetime.now().isoformat(),
        "jobs_curated": load_json(JOBS_CURATED_FILE, {}),
        "applications": load_json(app_tracker_file, {}),
        "feedback_log": load_json(feedback_file, {}),
        "resume_metadata": resume_meta,
        "auto_improvement_log": load_json(AUTO_LOG_FILE, {})
    }

    json_str = json.dumps(export_payload, indent=2)
    from flask import Response
    return Response(
        json_str,
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=gethired_backup.json"}
    )

def parse_datetime_safely(val):
    if not val or not isinstance(val, str):
        return None
    val = val.strip()
    if not val:
        return None

    dt = None
    try:
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
    except Exception:
        pass

    if dt is None:
        common_formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%Y/%m/%d",
            "%a, %d %b %Y %H:%M:%S %Z",
            "%a, %d %b %Y %H:%M:%S GMT"
        ]
        for fmt in common_formats:
            try:
                dt = datetime.strptime(val, fmt)
                break
            except Exception:
                pass

    if dt is not None:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
    return dt

@app.route("/api/daily-digest", methods=["GET", "POST"])
def manage_daily_digest():
    cfg = load_json(CONFIG_FILE, {})
    digest_cfg = cfg.get("daily_digest", {})
    last_viewed = digest_cfg.get("last_viewed_timestamp")

    if request.method == "POST":
        now_iso = datetime.now().isoformat()
        digest_cfg["last_viewed_timestamp"] = now_iso
        cfg["daily_digest"] = digest_cfg
        save_json(CONFIG_FILE, cfg)
        return jsonify({"message": "Daily digest timestamp updated", "last_viewed_timestamp": now_iso})

    curated_data = load_json(JOBS_CURATED_FILE, {})
    jobs = curated_data.get("jobs", [])

    min_score = cfg.get("background_search", {}).get("default_filters", {}).get("min_match_score", 50)
    new_jobs_count = 0
    high_matches_count = 0

    if last_viewed:
        lv_dt = parse_datetime_safely(last_viewed)
        if lv_dt:
            for j in jobs:
                first_seen = j.get("first_seen")
                fs_dt = parse_datetime_safely(first_seen) if first_seen else None
                if fs_dt and fs_dt > lv_dt:
                    new_jobs_count += 1
                    if _get_job_match_score(j, 0) >= min_score:
                        high_matches_count += 1
        else:
            new_jobs_count = len(jobs)
            high_matches_count = len([j for j in jobs if _get_job_match_score(j, 0) >= min_score])
    else:
        new_jobs_count = len(jobs)
        high_matches_count = len([j for j in jobs if _get_job_match_score(j, 0) >= min_score])

    tracker = ApplicationTracker()
    all_apps = tracker.list_applications()
    now_dt = datetime.now(timezone.utc)
    followup_count = 0
    for app_item in all_apps:
        if app_item.get("status") == "interviewed":
            last_upd = app_item.get("applied_date")
            history = app_item.get("status_history", [])
            if history:
                last_upd = history[-1].get("timestamp")
            if last_upd:
                l_dt = parse_datetime_safely(last_upd)
                if l_dt:
                    if (now_dt - l_dt).days >= 5:
                        followup_count += 1
                else:
                    pass

    return jsonify({
        "last_viewed_timestamp": last_viewed,
        "new_jobs_count": new_jobs_count,
        "high_matches_count": high_matches_count,
        "total_jobs": len(jobs),
        "followup_applications_count": followup_count
    })

@app.route("/api/notifications/config", methods=["GET", "POST"])
def manage_notifications_config():
    cfg = load_json(CONFIG_FILE, {})
    if request.method == "POST":
        body = request.get_json(force=True) or {}
        cfg["notifications"] = {
            "enabled": bool(body.get("enabled", False)),
            "match_score_threshold": int(body.get("match_score_threshold", 90))
        }
        save_json(CONFIG_FILE, cfg)
        return jsonify(cfg["notifications"])
    else:
        return jsonify(cfg.get("notifications", {"enabled": False, "match_score_threshold": 90}))

@app.route("/api/job/<job_id>/check-duplicate", methods=["POST", "GET"])
def check_duplicate_application(job_id):
    jobs_data = load_json(JOBS_FILE, {"jobs": []})
    curated_data = load_json(JOBS_CURATED_FILE, {"jobs": []})
    all_jobs = jobs_data.get("jobs", []) + curated_data.get("jobs", [])
    target_job = next((j for j in all_jobs if j.get("id") == job_id), None)
    if not target_job:
        return jsonify({"is_duplicate": False})

    comp_target = target_job.get("company", "").lower().strip()
    title_target = target_job.get("title", "").lower().strip()

    tracker = ApplicationTracker()
    user_apps = tracker.list_applications()
    now_dt = datetime.now()

    duplicate_app = None
    for app_rec in user_apps:
        if app_rec.get("status") == "archived":
            continue
        comp_app = app_rec.get("company", "").lower().strip()
        title_app = app_rec.get("job_title", "").lower().strip()

        if comp_target in comp_app or comp_app in comp_target:
            t1_words = set(title_target.split())
            t2_words = set(title_app.split())
            if t1_words and t2_words:
                overlap = len(t1_words.intersection(t2_words)) / max(len(t1_words), len(t2_words))
                if overlap >= 0.4 or title_target in title_app or title_app in title_target:
                    applied_date_str = app_rec.get("applied_date")
                    if applied_date_str:
                        try:
                            ad_dt = datetime.fromisoformat(applied_date_str.replace("Z", "+00:00"))
                            if (now_dt - ad_dt).days <= 30:
                                duplicate_app = app_rec
                                break
                        except Exception:
                            duplicate_app = app_rec
                            break
                    else:
                        duplicate_app = app_rec
                        break

    if duplicate_app:
        return jsonify({
            "is_duplicate": True,
            "existing_application": duplicate_app,
            "warning_message": f"You applied to a similar role at {duplicate_app.get('company')} on {duplicate_app.get('applied_date', '')[:10]} - continue anyway?"
        })

    return jsonify({"is_duplicate": False})

def _company_discovery_loop():
    """Autonomous company-list growth (no human in the loop).

    Proposes candidates (mined from collected postings + LLM by category),
    verifies each live against its ATS API, and admits only companies with
    India openings AND fresher-eligible openings right now. Every decision
    is logged to company_discovery_log.json.

    Deliberately offset from the scan loop and paced slowly: it shares the
    LLM key pool with scoring, and additions are capped per cycle.
    """
    time.sleep(900)  # let startup scan + scoring settle first
    while True:
        try:
            from company_discovery import run_discovery_cycle
            from llm_router import LLMRouter
            run_discovery_cycle(llm_router=LLMRouter(), use_llm=True)
        except Exception as e:
            print(f"[CompanyDiscovery] cycle error: {e}")
        # 2h cadence (was 6h): the 75% fresher-active goal needs ~630 more
        # fresher-active companies; at 4 cycles/day the probe volume was the
        # bottleneck, not LLM quota (1 proposal call per cycle).
        time.sleep(7200)


if __name__ == "__main__":
    # Restored from master: the debug/gethired-stability branch removed this
    # thread start, leaving the real career-page scanner (ScanCoordinator /
    # BrowserScanner) defined but never invoked — so the app could never
    # discover jobs. (fetch_career_pages in background_search_worker only
    # re-reads jobs_store.json; this thread is what actually fills it.)
    scan_thread = threading.Thread(target=background_scanner_loop, daemon=True)
    scan_thread.start()

    # Autonomous company-list growth + verification (see _company_discovery_loop)
    discovery_thread = threading.Thread(target=_company_discovery_loop, daemon=True)
    discovery_thread.start()

    port = int(os.environ.get("PORT", 5050))
    print(f"Starting GetHired Flask server on http://127.0.0.1:{port}...")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
