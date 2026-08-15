import re
from urllib.parse import urlparse
from typing import List, Dict, Any, Tuple

# Domain mapping rules for integrity validation
COMPANY_DOMAIN_PATTERNS = {
    "amazon": ["amazon.com", "amazon.jobs", "aws.amazon.com"],
    "aws": ["amazon.com", "amazon.jobs", "aws.amazon.com"],
    "google": ["google.com", "careers.google.com"],
    "microsoft": ["microsoft.com", "careers.microsoft.com"],
    "flipkart": ["flipkart.com", "flipkartcareers.com"],
    "swiggy": ["swiggy.com", "lever.co"],
    "zomato": ["zomato.com"],
    "razorpay": ["razorpay.com", "greenhouse.io"],
}

SYNTHETIC_ID_PATTERN = re.compile(r".*job-career-\d+-\d+.*")

NON_INDIA_RE = re.compile(
    r"\b(china|nigeria|milan|ita|italy|seattle|berlin|tokyo|toyko|melbourne|sydney|london|suzhou|lagos|dublin|usa|united states|australia|germany|japan|canada|france|brazil|south africa|uk|gbr)\b",
    re.I
)

INDIA_RE = re.compile(
    r"\b(india|ind|bangalore|bengaluru|hyderabad|gurugram|gurgaon|noida|pune|mumbai|delhi|chennai|kolkata|ahmedabad|kochi)\b",
    re.I
)

STATIC_PAGE_BLACKLIST = [
    "leadership principles", "our workplace", "about us", "terms of use",
    "privacy notice", "cookie preferences", "contact us", "read more",
    "see all jobs", "view all", "learn more", "how we hire", "workplace",
    "privacy policy", "terms of service", "site map", "sitemap", "faqs",
    "frequently asked questions", "help center", "support page", "contact support",
    "cookie policy", "customer support portal", "get support", "support center",
    "travel insurance", "international travel insurance", "internet banking",
    "payment gateway", "international payment gateway", "subscriptions",
    "credit card", "savings account", "fixed deposit", "car insurance",
    "bike insurance", "health insurance", "personal loan", "home loan",
    "wealth managers & financial advisors", "flight tickets", "water bill",
    "international payment solution", "accept international payments",
    "apple leadership", "executive team", "our team", "executive leadership",
    "leadership programme", "leadership program", "founders & leadership",
    "our leadership", "company leadership", "senior leadership", "management team",
    "board of directors", "investor relations", "newsroom", "press releases",
    "our history", "who we are", "why choose us", "life at", "culture",
    "diversity & inclusion", "thought leadership", "leadership team",
    "industry analyst recognition", "analyst recognition", "analyst reports",
    "awards and recognition", "honoring leadership", "accolades", "recognition",
    "discord community", "slack community", "community", "join discord",
    "follow us", "connect on linkedin", "twitter community", "telegram channel",
    "developer docs", "developer documentation", "api documentation", "api reference",
    "user guide", "developer guide", "documentation", "privacy policy", "terms of service"
]

EXACT_TITLE_BLACKLIST = {
    "leadership", "leaders", "analysts", "leadership team", "our team", "about us",
    "contact us", "careers", "jobs", "home", "search jobs", "view open positions",
    "join our team", "explore careers", "internship programs", "free internship programs",
    "engineering", "engineering & data", "international", "founders & leadership",
    "executive leadership", "leadership programme", "leadership program", "founders"
}

JOB_TITLE_KEYWORDS = re.compile(
    r"\b(engineer|developer|dev|intern|manager|analyst|designer|consultant|associate|"
    r"specialist|lead|architect|sde|swe|head|director|vice president|vp|officer|"
    r"executive|trainee|recruiter|accountant|representative|administrator|coordinator|"
    r"scientist|fellow|partner|staff|principal)\b",
    re.I
)

JOB_URL_PATTERNS = [
    "/job/", "/jobs/", "jobid", "job_id", "vjob", "detail", "viewjob",
    "p=", "gh_jid", "req", "apply", "requisition", "myworkdayjobs.com",
    "greenhouse.io", "lever.co", "ashbyhq", "smartrecruiters", "bamboohr",
    "workable.com", "taleo.net", "icims.com", "param.ai", "careers.google.com/jobs",
    "careers.microsoft.com/us/en/job", "amazon.jobs/en/jobs/", "jobdetails"
]

def check_job_posting_validity(job: Dict[str, Any]) -> Tuple[bool, List[str]]:
    reasons = []
    comp = str(job.get("company", "")).strip()
    title = str(job.get("title", "")).strip()
    url = str(job.get("url", "")).strip()
    loc = str(job.get("location", "")).strip()
    pdate = job.get("posted_date")
    
    title_lower = title.lower()
    url_lower = url.lower()
    
    if not title or len(title) < 3:
        return False, ["Title too short or missing"]
        
    # 1. Non-Job Title Patterns (Word-Boundary Safe Regex)
    non_job_title_re = re.compile(
        r"\b("
        r"executive post graduate certificate|certification program|bootcamp in|diploma in|degree in|"
        r"summit|webinar|hackathon|workshop|conference|talk show|"
        r"talent community|talent network|talent pool|expression of interest|future opportunities"
        r")\b",
        re.I
    )
    if non_job_title_re.search(title):
        reasons.append(f"Title matches non-job listing pattern: '{title}'")

    # 2. Non-Job URL Subdomain and Path Patterns
    parsed_url = urlparse(url)
    url_host_and_path = (parsed_url.netloc + parsed_url.path).lower()
    non_job_url_patterns = ["/blog/", "blog.", "/news/", "news.", "/press/", "press.", "/events/", "events.", "/courses/", "courses.", "/certificate/", "/webinar/", "/summit/", "/docs/", "/docs", "docs.", "/documentation/", "/api-reference/", "/help/", "/support/"]
    for path_pat in non_job_url_patterns:
        if path_pat in url_host_and_path:
            reasons.append(f"URL hostname/path indicates non-job article/event/course: '{path_pat}' in '{url_host_and_path}'")
            break

    # 3. Blacklist check
    if title_lower in EXACT_TITLE_BLACKLIST:
        reasons.append(f"Title is exact non-job text: '{title}'")
    else:
        for bl in STATIC_PAGE_BLACKLIST:
            if bl in title_lower:
                reasons.append(f"Title contains blacklisted term: '{bl}'")
                break

    # 4. Positive Signal Requirement Check:
    has_location = bool(loc and loc.lower() not in ["n/a", "none", "", "unspecified"])
    has_date = bool(pdate and str(pdate).strip())
    has_job_url = any(pat in url_lower for pat in JOB_URL_PATTERNS)
    has_title_kw = bool(JOB_TITLE_KEYWORDS.search(title_lower))

    if not has_location and not has_date and not has_job_url:
        reasons.append("Lacks all 3 positive job signals")

    if not has_title_kw and not has_job_url and not has_date:
        reasons.append("No job title structure/keyword and no job URL pattern")

    # 5. Location scope check
    if "India" in comp or "india" in url_lower or comp == "Amazon India":
        loc_and_title = f"{loc} {title}"
        if NON_INDIA_RE.search(loc_and_title) and not INDIA_RE.search(loc_and_title):
            reasons.append(f"Non-India location '{loc}' for India-scoped company '{comp}'")

    is_valid = len(reasons) == 0
    return is_valid, reasons

def validate_jobs_store_integrity(jobs: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    errors = []
    for idx, job in enumerate(jobs):
        jid = str(job.get("id", ""))
        comp = str(job.get("company", ""))
        url = str(job.get("url", ""))

        if SYNTHETIC_ID_PATTERN.match(jid) or "job-career-" in jid:
            errors.append(f"Job #{idx} ID '{jid}' ({comp}) matches forbidden synthetic pattern 'job-career-'.")

        valid_posting, posting_reasons = check_job_posting_validity(job)
        if not valid_posting:
            errors.append(f"Job #{idx} ({jid}): {', '.join(posting_reasons)}")

    is_valid = len(errors) == 0
    return is_valid, errors

def enforce_jobs_store_safeguard(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    clean_jobs = []
    for job in jobs:
        jid = str(job.get("id", ""))
        if SYNTHETIC_ID_PATTERN.match(jid) or "job-career-" in jid:
            continue
        valid_posting, _ = check_job_posting_validity(job)
        if valid_posting:
            clean_jobs.append(job)
    return clean_jobs
