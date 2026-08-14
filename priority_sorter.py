from typing import List, Dict, Any
from company_analyzer import CompanyAnalyzer

def matches_global_filters(job: Dict[str, Any], global_filters: Dict[str, Any] = None) -> bool:
    if not global_filters or not isinstance(global_filters, dict):
        return True

    title = (job.get("title") or "").lower()
    location = (job.get("location") or "").lower()
    company = (job.get("company") or "").lower()
    description = (job.get("description") or "").lower()
    job_type = str(job.get("job_type") or "").lower()
    job_skills = [s.lower() for s in (job.get("skills") or [])]
    combined = f"{title} {location} {company} {description} {job_type} {' '.join(job_skills)}"

    # Normalize category group keys mapping
    normalized_gf = {}
    for key, vals in global_filters.items():
        if not vals:
            continue
        if isinstance(vals, str):
            vals = [vals]
        clean_vals = [str(v).lower().strip() for v in vals if v and str(v).strip()]
        if not clean_vals:
            continue
        
        k_norm = key.lower().replace(" & ", "_").replace(" ", "_")
        normalized_gf[k_norm] = clean_vals

    for k_norm, clean_vals in normalized_gf.items():
        # Category specific checks with fallback to combined string match
        cat_matched = False
        for target in clean_vals:
            if target in combined:
                cat_matched = True
                break
            if k_norm in ["skills_tech_stack", "skills"] and any(target in s for s in job_skills):
                cat_matched = True
                break
            if k_norm in ["location_model", "location"] and target in location:
                cat_matched = True
                break
            if k_norm in ["role_seniority", "role"] and target in title:
                cat_matched = True
                break
        if not cat_matched:
            return False

    return True

class PrioritySorter:
    def __init__(self, target_locations: Any = None, global_filters: Dict[str, Any] = None):
        self.location_priority = target_locations or [["Gurugram", "Bangalore"], ["Delhi", "Noida"], ["Remote"]]
        self.global_filters = global_filters or {}

    def _get_location_priority_idx(self, job: Dict[str, Any]) -> int:
        if not self.location_priority:
            return 0

        title_lower = (job.get("title") or "").lower()
        loc_lower = (job.get("location") or "").lower()
        company_lower = (job.get("company") or "").lower()
        desc_lower = (job.get("description") or "").lower()
        skills = [s.lower() for s in (job.get("skills") or [])]
        combined = f"{title_lower} {loc_lower} {company_lower} {desc_lower} {' '.join(skills)}"

        for idx, tier in enumerate(self.location_priority):
            if isinstance(tier, dict):
                groups = tier.get("category_groups", [])
                for g in groups:
                    vals = g.get("values", [])
                    for target in vals:
                        if target and isinstance(target, str):
                            t_lower = target.lower().strip()
                            if t_lower and (t_lower in combined or t_lower in loc_lower or t_lower in title_lower):
                                return idx
            elif isinstance(tier, list):
                for target in tier:
                    if target and isinstance(target, str):
                        t_lower = target.lower().strip()
                        if t_lower and (t_lower in combined or t_lower in loc_lower):
                            return idx
            elif isinstance(tier, str):
                t_lower = tier.lower().strip()
                if t_lower and (t_lower in combined or t_lower in loc_lower):
                    return idx

        return len(self.location_priority)

    def sort_jobs(self, jobs: List[Dict[str, Any]], global_filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        gf_to_use = global_filters if global_filters is not None else self.global_filters
        
        # 1. First apply global_filters as a hard AND filter
        filtered_jobs = [j for j in jobs if matches_global_filters(j, gf_to_use)]

        # 2. Rank within the globally-filtered set based on priority tiers
        for job in filtered_jobs:
            loc_idx = self._get_location_priority_idx(job)
            job["location_priority"] = loc_idx

        def sort_key(j):
            match_score = j.get("match", {}).get("score", 0) if j.get("match") else 0
            ts = j.get("scan_timestamp") or j.get("first_seen_at") or j.get("first_seen") or ""
            return (
                j.get("location_priority", 99),
                -match_score,
                ts
            )

        return sorted(filtered_jobs, key=sort_key)

    def sort_by_company_affinity(self, jobs: List[Dict[str, Any]], applications_data: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Boosts jobs from companies with prior applications weighted by success_rate.
        - High success rate: boosted higher (+35 points)
        - Prior applications with moderate success: (+20 points)
        - 100% rejection history: slightly deprioritized (-5 points)
        - Never applied to: neutral (0 boost)
        """
        analyzer = CompanyAnalyzer()
        
        company_affinity_scores = {}
        for job in jobs:
            company_name = job.get("company", "")
            if company_name and company_name not in company_affinity_scores:
                analysis = analyzer.analyze_company(company_name)
                total_apps = analysis.get("total_applications", 0)
                succ_rate = analysis.get("success_rate", 0.0)
                rejections = analysis.get("status_breakdown", {}).get("rejected", 0)

                if total_apps == 0:
                    boost = 0.0
                elif succ_rate >= 0.50:
                    boost = 35.0
                elif succ_rate > 0.0:
                    boost = 20.0
                elif rejections == total_apps:
                    boost = -5.0
                else:
                    boost = 10.0

                company_affinity_scores[company_name] = boost

        def affinity_key(j):
            base_score = j.get("match", {}).get("score", 50) if j.get("match") else 50
            comp_boost = company_affinity_scores.get(j.get("company", ""), 0.0)
            final_affinity_score = base_score + comp_boost
            return (-final_affinity_score, -base_score, j.get("company", ""))

        return sorted(jobs, key=affinity_key)
