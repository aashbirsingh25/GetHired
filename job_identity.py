import re
import urllib.parse
import hashlib
from typing import Dict, Any, Optional

# Irrelevant tracking parameters that must be removed from URLs
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_cid", "utm_reader",
    "source", "ref", "referrer", "tracking", "session", "session_id", "fbclid", "gclid", "msclkid",
    "spm", "src", "trk", "trkParams", "mode", "from", "via", "_hsenc", "_hsmi", "mc_cid", "mc_eid"
}

LOCATION_ALIASES = {
    "bengaluru": "bangalore",
    "gurgaon": "gurugram",
    "noida": "noida",
    "hyderabad": "hyderabad",
    "mumbai": "mumbai",
    "delhi": "delhi",
    "new delhi": "delhi",
    "delhi ncr": "gurugram",
    "remote": "remote",
    "work from home": "remote"
}

def clean_company_name(company: str) -> str:
    if not company:
        return "unknown"
    c = str(company).lower().strip()
    # Strip common corporate suffixes
    c = re.sub(r"\b(inc|incorporated|ltd|limited|pvt|private|llp|corp|corporation|co|technologies|solutions|india|services)\b\.?", "", c, flags=re.IGNORECASE)
    c = re.sub(r"[^\w\s]", "", c)
    c = re.sub(r"\s+", " ", c).strip()
    return c if c else "unknown"

def clean_job_title(title: str) -> str:
    if not title:
        return "unknown"
    t = str(title).lower().strip()
    # Replace unicode dash variations with standard dash
    t = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]", "-", t)
    # Standardize common abbreviations / punctuation
    t = re.sub(r"[\(\)\[\]\{\}]", " ", t)
    t = re.sub(r"[^\w\s\-]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t if t else "unknown"

def clean_location(location: str) -> str:
    if not location:
        return "india"
    loc = str(location).lower().strip()
    loc = re.sub(r"[^\w\s]", " ", loc)
    loc = re.sub(r"\s+", " ", loc).strip()
    
    for alias, canonical in LOCATION_ALIASES.items():
        if alias in loc:
            return canonical
    return loc if loc else "india"

def normalize_url(url: str) -> str:
    if not url or not isinstance(url, str):
        return ""
    url = url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url

    try:
        parsed = urllib.parse.urlparse(url)
        # Scheme + netloc lowercased
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        
        # Filter query params
        query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
        clean_pairs = []
        for k, v in query_pairs:
            k_lower = k.lower()
            if k_lower not in TRACKING_PARAMS and not k_lower.startswith("utm_"):
                clean_pairs.append((k, v))

        # Reconstruct query string deterministically
        clean_query = urllib.parse.urlencode(sorted(clean_pairs))
        
        canonical = urllib.parse.urlunparse((parsed.scheme.lower(), netloc, path, parsed.params, clean_query, ""))
        return canonical
    except Exception:
        return url.lower()

def extract_req_id(job: Dict[str, Any]) -> str:
    req_id = job.get("req_id") or job.get("requisition_id") or job.get("job_code") or ""
    if req_id:
        return str(req_id).strip().lower()
    
    # Try extracting req id from URL or description if present
    url = job.get("url") or ""
    match = re.search(r"\b(req[-_]?\d+|\d{5,10})\b", url, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return ""

def generate_canonical_job_id(job: Dict[str, Any]) -> str:
    """
    Generates a deterministic SHA256 canonical job ID based on identity hierarchy:
    1. Stable source-native job ID (if native & verified)
    2. Normalized canonical job URL
    3. Deterministic Fingerprint: company | title | location | req_id
    """
    # 1. Canonicalized URL
    raw_url = job.get("url") or job.get("canonical_url") or ""
    canon_url = normalize_url(raw_url)
    
    # Check if native source id is available & truly specific
    source_id = job.get("source_id") or job.get("native_id")
    source = (job.get("source") or "").lower()
    if source_id and source and source not in ["custom", "web", "search"]:
        raw_key = f"native:{source}:{str(source_id).strip()}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]

    if canon_url and len(canon_url) > 12:
        raw_key = f"url:{canon_url}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]

    # 3. Deterministic Fingerprint
    norm_comp = clean_company_name(job.get("company", ""))
    norm_title = clean_job_title(job.get("title", ""))
    norm_loc = clean_location(job.get("location", ""))
    req_id = extract_req_id(job)

    fp = f"fp:{norm_comp}|{norm_title}|{norm_loc}"
    if req_id:
        fp += f"|{req_id}"

    return hashlib.sha256(fp.encode("utf-8")).hexdigest()[:16]
