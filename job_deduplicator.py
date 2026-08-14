import re
import difflib
from typing import List, Dict, Any, Tuple
from job_identity import (
    generate_canonical_job_id,
    normalize_url,
    clean_company_name,
    clean_job_title,
    clean_location,
    extract_req_id
)

from store_integrity_checker import check_job_posting_validity

def token_set_ratio(s1: str, s2: str) -> float:
    t1 = set(s1.lower().split())
    t2 = set(s2.lower().split())
    if not t1 or not t2:
        return 0.0
    intersection = t1.intersection(t2)
    return len(intersection) / max(len(t1), len(t2))

class JobDeduplicator:
    def __init__(self):
        pass

    def deduplicate(self, jobs: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        if not jobs:
            return [], {"total_raw": 0, "total_normalized": 0, "duplicates_removed": 0, "unique_jobs": 0}

        total_raw = len(jobs)
        normalized_jobs = []

        # 1. Normalize every job and ensure canonical job_id
        for raw_job in jobs:
            valid_posting, _ = check_job_posting_validity(raw_job)
            if not valid_posting:
                continue

            job = dict(raw_job)
            canon_id = generate_canonical_job_id(job)
            canon_url = normalize_url(job.get("url") or job.get("canonical_url") or "")
            
            job["id"] = canon_id
            job["job_id"] = canon_id
            job["canonical_url"] = canon_url or job.get("url", "")
            job["normalized_company"] = clean_company_name(job.get("company", ""))
            job["normalized_title"] = clean_job_title(job.get("title", ""))
            job["normalized_location"] = clean_location(job.get("location", ""))
            job["req_id"] = extract_req_id(job)

            if "sources" not in job or not job["sources"]:
                src = job.get("source") or "web"
                job["sources"] = [src]
            
            if "source_ids" not in job or not isinstance(job["source_ids"], dict):
                src = job.get("source") or "web"
                job["source_ids"] = {src: raw_job.get("id") or canon_id}

            normalized_jobs.append(job)

        total_normalized = len(normalized_jobs)

        # 2. Stage A & B & C: Group by canonical ID / URL / Fingerprint
        canonical_map: Dict[str, List[Dict[str, Any]]] = {}

        for job in normalized_jobs:
            cid = job["id"]
            if cid not in canonical_map:
                canonical_map[cid] = []
            canonical_map[cid].append(job)

        # Merge clusters formed by exact canonical ID
        stage1_merged = [self._merge_job_cluster(cluster) for cluster in canonical_map.values()]

        # 3. Stage D: Near-Duplicate Detection across distinct canonical IDs
        final_clusters: List[List[Dict[str, Any]]] = []

        for job in stage1_merged:
            placed = False
            norm_comp = job["normalized_company"]
            norm_title = job["normalized_title"]
            norm_loc = job["normalized_location"]
            req_id = job["req_id"]

            for cluster in final_clusters:
                head = cluster[0]
                head_comp = head["normalized_company"]
                head_title = head["normalized_title"]
                head_loc = head["normalized_location"]
                head_req = head["req_id"]

                # If requisition IDs are both present and different, they are distinct jobs
                if req_id and head_req and req_id != head_req:
                    continue

                # Company match
                comp_match = (norm_comp == head_comp) or (difflib.SequenceMatcher(None, norm_comp, head_comp).ratio() > 0.85)

                # Location match
                loc_match = (norm_loc == head_loc) or ("remote" in norm_loc and "remote" in head_loc) or (difflib.SequenceMatcher(None, norm_loc, head_loc).ratio() > 0.80)

                # Title match
                title_ratio = token_set_ratio(norm_title, head_title)
                diff_ratio = difflib.SequenceMatcher(None, norm_title, head_title).ratio()
                title_match = (title_ratio >= 0.85 or diff_ratio >= 0.85)

                if comp_match and loc_match and title_match:
                    cluster.append(job)
                    placed = True
                    break

            if not placed:
                final_clusters.append([job])

        final_jobs = [self._merge_job_cluster(cluster) for cluster in final_clusters]

        duplicates_removed = total_raw - len(final_jobs)
        metrics = {
            "total_raw": total_raw,
            "total_normalized": total_normalized,
            "duplicates_removed": duplicates_removed,
            "unique_jobs": len(final_jobs)
        }

        print(f"[JobDeduplicator] Deduplication complete: {metrics}")
        return final_jobs, metrics

    def _merge_job_cluster(self, cluster: List[Dict[str, Any]]) -> Dict[str, Any]:
        if len(cluster) == 1:
            return cluster[0]

        # Select best base job: Highest parse confidence, longest description, earliest first_seen
        def sort_key(j):
            conf = j.get("parse_confidence", 0.5)
            desc_len = len(j.get("description", "") or "")
            seen = j.get("first_seen") or "9999-99-99"
            return (conf, desc_len, -hash(seen))

        sorted_cluster = sorted(cluster, key=sort_key, reverse=True)
        primary = dict(sorted_cluster[0])

        # Merge sources, source_ids, scan history, first_seen, last_seen
        all_sources = []
        all_source_ids = {}

        earliest_first_seen = primary.get("first_seen") or primary.get("posted_date")
        latest_last_seen = primary.get("last_seen") or primary.get("first_seen")

        for job in cluster:
            # Sources
            job_sources = job.get("sources") or [job.get("source", "web")]
            for s in job_sources:
                if s not in all_sources:
                    all_sources.append(s)

            # Source IDs
            job_sids = job.get("source_ids") or {}
            if isinstance(job_sids, dict):
                all_source_ids.update(job_sids)

            # Dates
            f_seen = job.get("first_seen") or job.get("posted_date")
            l_seen = job.get("last_seen") or job.get("first_seen")

            if f_seen and (not earliest_first_seen or f_seen < earliest_first_seen):
                earliest_first_seen = f_seen

            if l_seen and (not latest_last_seen or l_seen > latest_last_seen):
                latest_last_seen = l_seen

        primary["sources"] = all_sources
        primary["source_ids"] = all_source_ids
        primary["first_seen"] = earliest_first_seen
        primary["last_seen"] = latest_last_seen
        if "url" not in primary or not primary["url"]:
            primary["url"] = primary.get("canonical_url", "")

        return primary
