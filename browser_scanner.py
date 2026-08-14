import re
import time
import hashlib
from datetime import datetime
from typing import Tuple, List, Dict, Any
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from store_integrity_checker import check_job_posting_validity

def compute_parse_confidence_score(company: dict, jobs: list, method: str, historical_avg: float = 0.0) -> float:
    """
    Computes parse confidence score (0.0 to 1.0) based on extraction method and historical average yield comparison.
    """
    if not jobs or len(jobs) == 0:
        return 0.00

    if method == "workday_api":
        base_score = 0.95
    elif method == "stored_pattern":
        base_score = 0.90
    elif method == "heuristic":
        base_score = 0.75
    elif method == "apify":
        base_score = 0.90
    else:
        base_score = 0.60

    # Large sudden drop from historical average penalty
    if historical_avg > 0 and len(jobs) < (0.30 * historical_avg):
        base_score -= 0.30

    return max(0.00, min(1.00, round(base_score, 2)))

class BrowserScanner:
    def __init__(self, headless=True):
        self.headless = headless
        self._playwright = None
        self._browser = None

    def start(self):
        if not self._playwright:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-http2"
                ]
            )

    def close(self):
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None

    def scan_company(self, company: dict, stored_pattern: dict = None):
        """
        Scans a company's career page using Workday REST API, Playwright with HTTP/1.1 fallback, iframe ATS resolution, and JS widget waiting.
        Returns tuple: (jobs_list, used_pattern_dict, extraction_method, error_message)
        """
        url = company.get("career_url", "")
        company_name = company.get("name")
        company_id = company.get("id")

        # Direct Workday REST API Extractor (bypass SPA DOM empty state)
        if "myworkdayjobs.com" in url or company.get("ats") == "workday":
            workday_jobs = self._extract_workday_jobs(company, target_url=url)
            if workday_jobs:
                return workday_jobs, None, "workday_api", None

        self.start()

        context = self._browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        error_msg = None
        try:
            # Navigate with 15 second timeout
            page.goto(url, timeout=15000, wait_until="domcontentloaded")
            final_url = page.url

            # Check if page redirected to a Workday portal
            if "myworkdayjobs.com" in final_url and not ("myworkdayjobs.com" in url):
                workday_jobs = self._extract_workday_jobs(company, target_url=final_url)
                if workday_jobs:
                    context.close()
                    return workday_jobs, None, "workday_api", None

            # Dynamic pause for client-side JS rendering widgets (Groww/Darwinbox/React/Vue)
            page.wait_for_timeout(3500)
            html_content = page.content()
        except PlaywrightTimeoutError:
            error_msg = f"Timeout (15s) navigating to {url}"
            context.close()
            return [], None, "heuristic", error_msg
        except Exception as e:
            error_msg = f"Error opening page: {str(e)}"
            context.close()
            return [], None, "heuristic", error_msg


        soup = BeautifulSoup(html_content, "html.parser")
        now_iso = datetime.now().isoformat()

        # 1. Try stored pattern if available
        if stored_pattern:
            jobs = self._extract_with_pattern(soup, company, stored_pattern, now_iso)
            if len(jobs) >= 1:
                context.close()
                return jobs, stored_pattern, "stored_pattern", None

        # 2. Try Heuristic Extraction
        jobs, learned_pattern = self._extract_with_heuristics(soup, page, company, now_iso)

        # 3. If 0 jobs found, check for ATS iframe embeds (Porter / Greenhouse / Lever / Workday)
        if not jobs:
            ats_iframes = []
            for iframe in soup.find_all("iframe", src=True):
                src = iframe["src"]
                if any(ats_domain in src.lower() for ats_domain in ["greenhouse.io", "lever.co", "workday", "ashbyhq", "smartrecruiters", "bamboohr", "workable", "taleo", "icims", "param.ai", "job"]):
                    ats_iframes.append(self._fix_url(src, url))

            if ats_iframes:
                target_iframe_url = ats_iframes[0]
                try:
                    page.goto(target_iframe_url, timeout=12000, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)
                    iframe_html = page.content()
                    iframe_soup = BeautifulSoup(iframe_html, "html.parser")
                    jobs, learned_pattern = self._extract_with_heuristics(iframe_soup, page, company, now_iso)
                except Exception:
                    pass

        context.close()

        if len(jobs) >= 5:
            return jobs, learned_pattern, "heuristic", None
        elif len(jobs) > 0:
            # Low yield, heuristic partially worked
            return jobs, learned_pattern, "heuristic", None
        else:
            return [], None, "heuristic", "No jobs found on page using heuristic extraction"


    def _extract_with_pattern(self, soup: BeautifulSoup, company: dict, pattern: dict, timestamp: str):
        card_sel = pattern.get("job_card_selector")
        title_sel = pattern.get("title_selector")
        loc_sel = pattern.get("location_selector")
        link_sel = pattern.get("apply_link_selector")

        if not card_sel:
            return []

        cards = soup.select(card_sel)
        jobs = []

        for card in cards:
            title_el = card.select_one(title_sel) if title_sel else None
            loc_el = card.select_one(loc_sel) if loc_sel else None
            link_el = card.select_one(link_sel) if link_sel else None

            title = title_el.get_text(strip=True) if title_el else None
            location = loc_el.get_text(strip=True) if loc_el else "India"
            
            href = None
            if link_el and link_el.has_attr("href"):
                href = link_el["href"]
            elif card.name == "a" and card.has_attr("href"):
                href = card["href"]

            if title:
                resolved_url = self._fix_url(href, company["career_url"]) if href else company["career_url"]
                final_url, needs_review = self._validate_job_url(resolved_url, company["career_url"])
                job_id = self._generate_job_id(company["id"], title, final_url)
                
                cand_job = {
                    "id": job_id,
                    "company": company["name"],
                    "title": title,
                    "location": location,
                    "url": final_url,
                    "description": card.get_text(" ", strip=True)[:500],
                    "posted_date": None,
                    "extraction_method": "stored_pattern",
                    "scan_timestamp": timestamp,
                    "first_seen_at": timestamp,
                    "closed": False,
                    "needs_manual_link_review": needs_review,
                    "match": None
                }
                
                is_valid, _ = check_job_posting_validity(cand_job)
                if is_valid:
                    jobs.append(cand_job)
        return jobs

    def _extract_with_heuristics(self, soup: BeautifulSoup, page, company: dict, timestamp: str):
        """
        Generic heuristic search for job listings across common ATS structures and standard HTML layouts.
        """
        card_candidates = [
            ".opening", ".posting", "[data-qa='job-card']", ".job-card", ".job-item",
            ".job-listing", ".position-card", ".career-card", ".job-tile", ".job_card",
            "tr.job-row", "div.job-post", "li.job", "article.job", ".jobs-list-item",
            "[data-automation-id='jobPosting']", "[data-automation-id='jobTitle']",
            ".role-card", ".opening-card", ".css-19v252", "[data-ui-id='job-card']",
            "div[data-job-id]", "[class*='jobCard']", "[class*='JobCard']"
        ]

        for sel in card_candidates:
            cards = soup.select(sel)
            if len(cards) >= 1:
                jobs = []
                for card in cards:
                    # Look for title inside card
                    t_el = card.find(["h1", "h2", "h3", "h4", "a", "strong"])
                    title = t_el.get_text(strip=True) if t_el else ""
                    if len(title) < 3 or len(title) > 120:
                        continue

                    # Look for location
                    loc_el = card.find(class_=re.compile(r"loc|city|place|region|country", re.I))
                    raw_location = loc_el.get_text(strip=True) if loc_el else ""
                    location = raw_location if raw_location else "India"

                    # Look for link
                    link_el = card if card.name == "a" else (card.find("a", href=True) or card.find_parent("a", href=True))
                    href = link_el["href"] if link_el and link_el.has_attr("href") else None
                    page_url = getattr(page, "url", company.get("career_url", "")) if page else company.get("career_url", "")
                    resolved_url = self._fix_url(href, page_url) if href else company["career_url"]
                    final_url, needs_review = self._validate_job_url(resolved_url, company["career_url"])

                    job_id = self._generate_job_id(company["id"], title, final_url)
                    cand_job = {
                        "id": job_id,
                        "company": company["name"],
                        "title": title,
                        "location": location,
                        "url": final_url,
                        "description": card.get_text(" ", strip=True)[:500],
                        "posted_date": None,
                        "extraction_method": "heuristic",
                        "scan_timestamp": timestamp,
                        "first_seen_at": timestamp,
                        "closed": False,
                        "needs_manual_link_review": needs_review,
                        "match": None
                    }

                    is_valid, _ = check_job_posting_validity(cand_job)
                    if is_valid:
                        jobs.append(cand_job)

                if len(jobs) > 0:
                    learned_pattern = {
                        "job_card_selector": sel,
                        "title_selector": "h1, h2, h3, h4, a, strong",
                        "location_selector": "[class*='loc'], [class*='city']",
                        "apply_link_selector": "a"
                    }
                    return jobs, learned_pattern

        # Fallback: scan for any links/headings containing job keywords
        jobs = []
        links = soup.find_all("a", href=True)
        job_keywords = re.compile(r"\b(engineer|developer|intern|manager|analyst|designer|consultant|associate|specialist|lead|architect|sde|swe)\b", re.I)
        page_url = getattr(page, "url", company.get("career_url", "")) if page else company.get("career_url", "")
        
        seen_titles = set()
        for a in links:
            text = a.get_text(strip=True)
            if job_keywords.search(text) and len(text) >= 4 and len(text) <= 100:
                if text not in seen_titles:
                    seen_titles.add(text)
                    href = a["href"]
                    resolved_url = self._fix_url(href, page_url)
                    final_url, needs_review = self._validate_job_url(resolved_url, company["career_url"])
                    job_id = self._generate_job_id(company["id"], text, final_url)

                    cand_job = {
                        "id": job_id,
                        "company": company["name"],
                        "title": text,
                        "location": "India",
                        "url": final_url,
                        "description": text,
                        "posted_date": None,
                        "extraction_method": "heuristic",
                        "scan_timestamp": timestamp,
                        "first_seen_at": timestamp,
                        "closed": False,
                        "needs_manual_link_review": needs_review,
                        "match": None
                    }

                    is_valid, _ = check_job_posting_validity(cand_job)
                    if is_valid:
                        jobs.append(cand_job)

        learned_pattern = None
        if len(jobs) > 0:
            learned_pattern = {
                "job_card_selector": "a[href]",
                "title_selector": "self",
                "location_selector": None,
                "apply_link_selector": "self"
            }

        return jobs, learned_pattern

    def _generate_job_id(self, company_id: str, title: str, href: str) -> str:
        from job_identity import generate_canonical_job_id
        dummy_job = {"company": company_id, "title": title, "url": href}
        return generate_canonical_job_id(dummy_job)

    def _validate_location_scope(self, company: dict, location: str, title: str) -> bool:
        """
        Verifies that a job posting's location matches the company's regional scoping.
        For India-scoped companies ('India' in name or career_url), filters out non-India locations.
        """
        comp_name = company.get("name", "")
        career_url = company.get("career_url", "").lower()
        if "India" in comp_name or "india" in career_url:
            raw_loc = (location or "").lower()
            raw_title = (title or "").lower()
            combined = f"{raw_loc} {raw_title}"
            
            non_india_countries = [
                "china", "nigeria", "milan", "ita", "italy", "seattle", "berlin", "tokyo", "toyko",
                "melbourne", "sydney", "london", "suzhou", "lagos", "dublin", "irl", "deu", "aus",
                "gbr", "uk", "chn", "sgp", "usa", "u.s.a", "united states", "australia", "germany",
                "united kingdom", "japan", "canada", "france", "brazil", "south africa"
            ]
            india_keywords = [
                "india", "ind", "bangalore", "bengaluru", "hyderabad", "gurugram", "gurgaon",
                "noida", "pune", "mumbai", "delhi", "chennai", "kolkata", "ahmedabad", "kochi",
                "mh, ind", "ka, ind", "ts, ind", "dl, ind"
            ]
            
            has_non_india = any(re.search(r"\b" + re.escape(c) + r"\b", combined) or c in combined for c in non_india_countries)
            has_india = any(re.search(r"\b" + re.escape(ik) + r"\b", combined) or ik in combined for ik in india_keywords)
            
            if has_non_india and not has_india:
                return False
        return True

    def _validate_job_url(self, candidate_url: str, company_career_url: str) -> Tuple[str, bool]:
        """
        Validates candidate_url via HTTP check. Returns (validated_url, needs_manual_link_review).
        """
        if not candidate_url or candidate_url.strip() == "" or candidate_url.strip() == company_career_url.strip() or candidate_url.strip() == company_career_url.strip() + "/":
            return company_career_url, True

        import requests
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        try:
            r = requests.head(candidate_url, headers=headers, timeout=3.5, allow_redirects=True)
            if r.status_code not in (200, 301, 302, 307, 308):
                r = requests.get(candidate_url, headers=headers, timeout=3.5, allow_redirects=True, stream=True)

            if r.status_code in (200, 301, 302, 307, 308):
                return candidate_url, False
            else:
                return company_career_url, True
        except Exception:
            return company_career_url, True

    def _fix_url(self, href: str, base_url: str) -> str:
        if not href:
            return base_url
        href_str = str(href).strip()
        if not href_str or href_str.startswith("javascript:") or href_str == "#" or href_str.startswith("javascript;"):
            return base_url
        if href_str.startswith("http://") or href_str.startswith("https://"):
            return href_str
        from urllib.parse import urljoin
        return urljoin(base_url, href_str)

    def _extract_workday_jobs(self, company: dict, target_url: str = None) -> list:
        url = target_url or company.get("career_url", "")
        import requests
        m = re.search(r"https://([^/]+\.myworkdayjobs\.com)(?:/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+))?", url)
        if not m:
            return []

        host = m.group(1)
        site = m.group(2) or ("Workday" if "workday" in host else "Careers")
        tenant = host.split(".")[0]
        api_url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        
        now_iso = datetime.now().isoformat()
        jobs = []

        try:
            for offset in [0, 20, 40]:
                payload = {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""}
                r = requests.post(api_url, json=payload, headers=headers, timeout=8)
                if r.status_code == 200:
                    data = r.json()
                    postings = data.get("jobPostings", [])
                    if not postings:
                        break
                    for jp in postings:
                        title = jp.get("title", "")
                        loc = jp.get("locationsText") or "India"
                        ext_path = jp.get("externalPath", "")
                        full_url = f"https://{host}/en-US/{site}{ext_path}"
                        job_id = self._generate_job_id(company["id"], title, full_url)
                        cand_job = {
                            "id": job_id,
                            "company": company["name"],
                            "title": title,
                            "location": loc,
                            "url": full_url,
                            "description": f"Workday job posting: {title} ({loc})",
                            "posted_date": jp.get("postedOn"),
                            "extraction_method": "workday_api",
                            "scan_timestamp": now_iso,
                            "first_seen_at": now_iso,
                            "closed": False,
                            "needs_manual_link_review": False,
                            "match": None
                        }
                        is_valid, _ = check_job_posting_validity(cand_job)
                        if is_valid:
                            jobs.append(cand_job)
                else:
                    break
            return jobs
        except Exception as e:
            print(f"[BrowserScanner] Workday API extraction error for {company['name']}: {e}")
        return []


