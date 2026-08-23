import re
import time
import json
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
    def __init__(self, headless=True, enable_llm_learning=True):
        self.headless = headless
        self._playwright = None
        self._browser = None
        # LLM page-structure learning (last-resort fallback for arbitrary
        # career pages). One call per company; result persisted as a pattern.
        self.enable_llm_learning = enable_llm_learning
        self._llm_router = None

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

        # Direct ATS REST API Extractors (bypass SPA DOM empty state)
        ats_type = (company.get("ats") or "").lower()
        if "myworkdayjobs.com" in url or ats_type == "workday":
            workday_jobs, err = self._extract_workday_jobs(company, target_url=url, return_error=True)
            return workday_jobs, None, "workday_api", err
        elif "greenhouse.io" in url or ats_type == "greenhouse":
            gh_jobs, err = self._extract_greenhouse_jobs(company, target_url=url, return_error=True)
            return gh_jobs, None, "greenhouse_api", err
        elif "lever.co" in url or ats_type == "lever":
            lever_jobs, err = self._extract_lever_jobs(company, target_url=url, return_error=True)
            return lever_jobs, None, "lever_api", err
        elif "ashbyhq.com" in url or ats_type == "ashby":
            ashby_jobs, err = self._extract_ashby_jobs(company, target_url=url, return_error=True)
            return ashby_jobs, None, "ashby_api", err
        elif "smartrecruiters.com" in url or ats_type == "smartrecruiters":
            sr_jobs, err = self._extract_smartrecruiters_jobs(company, target_url=url, return_error=True)
            return sr_jobs, None, "smartrecruiters_api", err
        elif ".keka.com" in url or ats_type == "keka":
            keka_jobs, err = self._extract_keka_jobs(company, target_url=url, return_error=True)
            return keka_jobs, None, "keka_api", err

        self.start()

        context = self._browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()
        error_msg = None
        jobs = []
        learned_pattern = None
        method = "heuristic"
        llm_learned_method = False

        try:
            try:
                page.goto(url, timeout=15000, wait_until="domcontentloaded")
                final_url = page.url

                if "myworkdayjobs.com" in final_url and not ("myworkdayjobs.com" in url):
                    workday_jobs = self._extract_workday_jobs(company, target_url=final_url)
                    if workday_jobs:
                        return workday_jobs, None, "workday_api", None

                page.wait_for_timeout(3500)
                html_content = page.content()
            except PlaywrightTimeoutError:
                error_msg = f"Timeout (15s) navigating to {url}"
                return [], None, "heuristic", error_msg
            except Exception as e:
                error_msg = f"Error opening page: {str(e)}"
                return [], None, "heuristic", error_msg

            soup = BeautifulSoup(html_content, "html.parser")
            now_iso = datetime.now().isoformat()

            # 1. Try Schema.org JobPosting JSON-LD structured data
            json_ld_jobs = self._extract_json_ld_jobs(soup, company, now_iso)
            if len(json_ld_jobs) >= 1:
                return json_ld_jobs, None, "json_ld", None

            # 2. Try stored pattern if available
            if stored_pattern:
                p_jobs = self._extract_with_pattern(soup, company, stored_pattern, now_iso)
                if len(p_jobs) >= 1:
                    return p_jobs, stored_pattern, "stored_pattern", None

            # 3. Try Heuristic Extraction
            jobs, learned_pattern = self._extract_with_heuristics(soup, page, company, now_iso)

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

            # 4. LAST RESORT: ask an LLM to read the page and learn its
            # structure, then apply the selectors it returns. One call per
            # company - the learned pattern is persisted by ScanCoordinator
            # and reused deterministically on later scans.
            if not jobs and self.enable_llm_learning:
                try:
                    from llm_page_learner import learn_page_structure
                    if self._llm_router is None:
                        from llm_router import LLMRouter
                        self._llm_router = LLMRouter()
                    current_html = page.content()
                    learned = learn_page_structure(
                        current_html, page.url, company.get("name", ""), self._llm_router)
                    if learned:
                        llm_soup = BeautifulSoup(current_html, "html.parser")
                        llm_jobs = self._extract_with_pattern(llm_soup, company, learned, now_iso)
                        print(f"[LLMPageLearner] {company.get('name')}: selector "
                              f"'{learned['job_card_selector'][:40]}' -> {len(llm_jobs)} jobs")
                        if llm_jobs:
                            jobs = llm_jobs
                            learned_pattern = learned
                            llm_learned_method = True
                except Exception as llm_err:
                    print(f"[LLMPageLearner] {company.get('name')} failed: {str(llm_err)[:110]}")
        finally:
            try:
                context.close()
            except Exception:
                pass

        if len(jobs) >= 1:
            if llm_learned_method:
                method = "llm_learned"
            return jobs, learned_pattern, method, error_msg
        else:
            return [], None, "heuristic", error_msg or "Zero jobs extracted from page using heuristic extraction"


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

    def _extract_workday_jobs(self, company: Any, target_url: Any = None, return_error: bool = False):
        if isinstance(company, str):
            url = company
            company = {"id": "company", "name": "Company", "career_url": url}
        elif isinstance(target_url, bool):
            return_error = target_url
            target_url = None
            url = company.get("career_url", "") if isinstance(company, dict) else ""
        else:
            url = target_url or (company.get("career_url", "") if isinstance(company, dict) else "")
        import requests
        m = re.search(r"https://([^/]+\.myworkdayjobs\.com)(?:/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+))?", url)
        if not m:
            return ([], "Workday API: URL pattern did not match myworkdayjobs.com") if return_error else []

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
        error_msg = None

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

                        detail_url = f"https://{host}/wday/cxs/{tenant}/{site}/job{ext_path}"
                        desc = f"Workday job posting: {title} ({loc})"
                        try:
                            detail_r = requests.get(detail_url, headers=headers, timeout=4)
                            if detail_r.status_code == 200:
                                detail_data = detail_r.json()
                                job_desc_html = detail_data.get("jobDescription", "")
                                if job_desc_html:
                                    from bs4 import BeautifulSoup
                                    desc = BeautifulSoup(job_desc_html, "html.parser").get_text(separator=" ")
                        except Exception:
                            pass

                        job_id = self._generate_job_id(company["id"], title, full_url)
                        cand_job = {
                            "id": job_id,
                            "company": company["name"],
                            "title": title,
                            "location": loc,
                            "url": full_url,
                            "description": desc,
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
                    error_msg = f"Workday API HTTP {r.status_code}"
                    break
        except Exception as e:
            error_msg = f"Workday API extraction error: {e}"

        if return_error:
            return jobs, error_msg
        return jobs

    def _extract_json_ld_jobs(self, soup: BeautifulSoup, company: dict, timestamp: str) -> list:
        """
        Extracts Schema.org JobPosting objects embedded in <script type="application/ld+json">.
        Used universally by Google Careers, Greenhouse, Lever, Ashby, SmartRecruiters, and corporate portals.
        """
        jobs = []
        scripts = soup.find_all("script", type=re.compile(r"application/ld\+json", re.I))
        for script in scripts:
            if not script.string:
                continue
            try:
                data = json.loads(script.string)
                items = []
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    if data.get("@type") == "JobPosting":
                        items = [data]
                    elif "@graph" in data and isinstance(data["@graph"], list):
                        items = [item for item in data["@graph"] if isinstance(item, dict) and item.get("@type") == "JobPosting"]
                for item in items:
                    if not isinstance(item, dict) or item.get("@type") != "JobPosting":
                        continue
                    title = item.get("title") or item.get("name", "")
                    if not title or len(title) < 3:
                        continue

                    raw_loc = "India"
                    loc_obj = item.get("jobLocation")
                    if isinstance(loc_obj, dict):
                        address = loc_obj.get("address", {})
                        if isinstance(address, dict):
                            raw_loc = address.get("addressLocality") or address.get("addressRegion") or address.get("addressCountry") or "India"
                        elif isinstance(address, str):
                            raw_loc = address
                    elif isinstance(loc_obj, list) and loc_obj:
                        first_loc = loc_obj[0]
                        if isinstance(first_loc, dict):
                            address = first_loc.get("address", {})
                            if isinstance(address, dict):
                                raw_loc = address.get("addressLocality") or address.get("addressCountry") or "India"

                    url = item.get("url") or item.get("sameAs") or item.get("directApplyUrl") or company.get("career_url", "")
                    final_url = self._fix_url(url, company.get("career_url", ""))
                    desc = item.get("description") or title

                    job_id = self._generate_job_id(company["id"], title, final_url)
                    cand_job = {
                        "id": job_id,
                        "company": company["name"],
                        "title": title,
                        "location": str(raw_loc).strip(),
                        "url": final_url,
                        "description": str(desc)[:500],
                        "posted_date": item.get("datePosted"),
                        "extraction_method": "json_ld",
                        "scan_timestamp": timestamp,
                        "first_seen_at": timestamp,
                        "closed": False,
                        "needs_manual_link_review": False,
                        "match": None
                    }
                    is_valid, _ = check_job_posting_validity(cand_job)
                    if is_valid:
                        jobs.append(cand_job)
            except Exception:
                continue
        return jobs

    def _discover_ats_token(self, url: str) -> dict:
        if not url or not url.startswith("http"):
            return {}
        import requests
        try:
            r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}, timeout=6, allow_redirects=True)
            if r.status_code == 200:
                gh = re.search(r'boards\.greenhouse\.io/embed/job_board\?for=([a-zA-Z0-9_-]+)', r.text) or re.search(r'boards\.greenhouse\.io/([a-zA-Z0-9_-]+)', r.text)
                lev = re.search(r'jobs\.lever\.co/([a-zA-Z0-9_-]+)', r.text)
                ash = re.search(r'ashbyhq\.com/([a-zA-Z0-9_-]+)', r.text)
                sr = re.search(r'smartrecruiters\.com/([a-zA-Z0-9_-]+)', r.text)
                return {
                    'greenhouse': gh.group(1) if gh else None,
                    'lever': lev.group(1) if lev else None,
                    'ashby': ash.group(1) if ash else None,
                    'smartrecruiters': sr.group(1) if sr else None
                }
        except Exception:
            pass
        return {}

    def _extract_greenhouse_jobs(self, company: Any, target_url: Any = None, return_error: bool = False):
        if isinstance(company, str):
            url = company
            company = {"id": "company", "name": "Company", "career_url": url}
        elif isinstance(target_url, bool):
            return_error = target_url
            target_url = None
            url = company.get("career_url", "") if isinstance(company, dict) else ""
        else:
            url = target_url or (company.get("career_url", "") if isinstance(company, dict) else "")
        import requests

        clean_url = str(url).rstrip("/")
        m = re.search(r"for=([^&]+)", clean_url)
        if m:
            board_token = m.group(1)
        else:
            from urllib.parse import urlparse
            path = urlparse(clean_url).path
            prefixes = {"v0", "postings", "careers", "embed", "c", "boards", "job_board", "job-board", "job", "jobs"}
            segments = [s for s in path.split("/") if s and s not in prefixes]
            if segments:
                board_token = segments[0]
            else:
                board_token = ""

        if board_token in ["jobs", "careers", "job", "career", "board", "boards", "embed", "c", ""] or not board_token:
            board_token = company.get("id", "").lower().replace("-india", "").replace("_india", "").strip() if isinstance(company, dict) else ""

        if not board_token:
            return ([], "Greenhouse API: Could not resolve board token") if return_error else []

        api_url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true"
        now_iso = datetime.now().isoformat()
        jobs = []
        error_msg = None

        try:
            r = None
            for attempt in range(2):
                try:
                    r = requests.get(api_url, timeout=8)
                    break
                except requests.exceptions.RequestException as req_err:
                    if attempt == 1:
                        raise req_err
                    time.sleep(0.5)

            if r is not None and r.status_code == 200:
                data = r.json()
                for item in data.get("jobs", []):
                    title = item.get("title", "")
                    loc = (item.get("location") or {}).get("name") or "India"
                    full_url = item.get("absolute_url") or url

                    desc_html = item.get("content", "")
                    if desc_html:
                        from bs4 import BeautifulSoup
                        desc = BeautifulSoup(desc_html, "html.parser").get_text(separator=" ")
                    else:
                        desc = f"Greenhouse posting: {title}"

                    job_id = self._generate_job_id(company["id"], title, full_url)
                    cand_job = {
                        "id": job_id,
                        "company": company["name"],
                        "title": title,
                        "location": loc,
                        "url": full_url,
                        "description": desc,
                        "posted_date": item.get("updated_at"),
                        "extraction_method": "greenhouse_api",
                        "scan_timestamp": now_iso,
                        "first_seen_at": now_iso,
                        "closed": False,
                        "needs_manual_link_review": False,
                        "match": None
                    }
                    is_valid, _ = check_job_posting_validity(cand_job)
                    if is_valid:
                        jobs.append(cand_job)
            elif r is not None and r.status_code == 404:
                # Token auto-discovery fallback
                disc = self._discover_ats_token(url)
                disc_token = disc.get("greenhouse")
                if disc_token and disc_token != board_token:
                    disc_url = f"https://boards-api.greenhouse.io/v1/boards/{disc_token}/jobs?content=true"
                    r_disc = requests.get(disc_url, timeout=8)
                    if r_disc.status_code == 200:
                        data = r_disc.json()
                        for item in data.get("jobs", []):
                            title = item.get("title", "")
                            loc = (item.get("location") or {}).get("name") or "India"
                            full_url = item.get("absolute_url") or url
                            desc_html = item.get("content", "")
                            if desc_html:
                                from bs4 import BeautifulSoup
                                desc = BeautifulSoup(desc_html, "html.parser").get_text(separator=" ")
                            else:
                                desc = f"Greenhouse posting: {title}"
                            job_id = self._generate_job_id(company["id"], title, full_url)
                            cand_job = {
                                "id": job_id,
                                "company": company["name"],
                                "title": title,
                                "location": loc,
                                "url": full_url,
                                "description": desc,
                                "posted_date": item.get("updated_at"),
                                "extraction_method": "greenhouse_api",
                                "scan_timestamp": now_iso,
                                "first_seen_at": now_iso,
                                "closed": False,
                                "needs_manual_link_review": False,
                                "match": None
                            }
                            is_valid, _ = check_job_posting_validity(cand_job)
                            if is_valid:
                                jobs.append(cand_job)
                        return (jobs, None) if return_error else jobs
                error_msg = f"Greenhouse API HTTP 404 for board token '{board_token}'"
            elif r is not None:
                error_msg = f"Greenhouse API HTTP {r.status_code}"
            else:
                error_msg = "Greenhouse API request failed"
        except Exception as e:
            error_msg = f"Greenhouse API extraction error: {e}"

        if return_error:
            return jobs, error_msg
        return jobs

    def _extract_keka_jobs(self, company: dict, target_url: str = None, return_error: bool = False):
        """Extract jobs from a Keka-hosted careers portal.

        Keka (widely used by Indian startups/SMEs) exposes a public JSON
        endpoint on every tenant portal:
          https://<tenant>.keka.com/careers/api/jobs/default/active
        Discovered by observing the portal's own XHR calls; no key needed.
        """
        url = target_url or company.get("career_url", "")
        import requests
        from urllib.parse import urlparse

        host = urlparse(url).netloc
        tenant = ""
        if host.endswith(".keka.com"):
            tenant = host.split(".")[0]
        if not tenant:
            tenant = (company.get("id") or "").lower().replace("-india", "").strip()
        if not tenant:
            return ([], "Keka API: could not resolve tenant") if return_error else []

        api_url = f"https://{tenant}.keka.com/careers/api/jobs/default/active"
        portal_url = f"https://{tenant}.keka.com/careers/"
        now_iso = datetime.now().isoformat()
        jobs = []
        error_msg = None

        try:
            r = requests.get(api_url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}, timeout=10)
            if r is not None and r.status_code == 200:
                data = r.json()
                items = data if isinstance(data, list) else (data.get("data") or [])
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    title = (item.get("title") or "").strip()
                    if not title:
                        continue

                    locs = item.get("jobLocations") or []
                    loc_names = []
                    for l in locs:
                        if isinstance(l, dict):
                            loc_names.append(l.get("city") or l.get("name") or l.get("locationName") or "")
                        elif isinstance(l, str):
                            loc_names.append(l)
                    location = ", ".join([l for l in loc_names if l]) or "India"

                    job_ref = item.get("id")
                    full_url = f"{portal_url}{job_ref}/" if job_ref else portal_url

                    desc = item.get("description") or item.get("excerpt") or ""
                    if desc and "<" in desc:
                        from bs4 import BeautifulSoup
                        desc = BeautifulSoup(desc, "html.parser").get_text(separator=" ")
                    skills = item.get("skillNames") or []
                    exp = item.get("experience") or ""
                    desc_text = " ".join(str(x) for x in [desc, f"Experience: {exp}" if exp else "",
                                                          ("Skills: " + ", ".join(str(s) for s in skills)) if skills else ""]).strip()
                    if not desc_text:
                        desc_text = f"Keka posting: {title}"

                    job_id = self._generate_job_id(company["id"], title, full_url)
                    cand_job = {
                        "id": job_id,
                        "company": company["name"],
                        "title": title,
                        "location": location,
                        "url": full_url,
                        "description": desc_text,
                        "posted_date": item.get("publishedOn"),
                        "extraction_method": "keka_api",
                        "scan_timestamp": now_iso,
                        "first_seen_at": now_iso,
                        "closed": False,
                        "needs_manual_link_review": False,
                        "match": None
                    }
                    is_valid, _ = check_job_posting_validity(cand_job)
                    if is_valid:
                        jobs.append(cand_job)
            elif r is not None:
                error_msg = f"Keka API returned HTTP {r.status_code}"
        except Exception as e:
            error_msg = f"Keka API extraction error: {e}"

        return (jobs, error_msg) if return_error else jobs

    def _extract_lever_jobs(self, company: dict, target_url: str = None, return_error: bool = False):
        url = target_url or company.get("career_url", "")
        import requests

        clean_url = url.rstrip("/")
        from urllib.parse import urlparse
        path = urlparse(clean_url).path
        prefixes = {"v0", "postings", "careers", "embed", "c", "boards", "job_board", "job-board", "job"}
        segments = [s for s in path.split("/") if s and s not in prefixes]
        if segments:
            site = segments[0]
        else:
            site = ""

        if site in ["jobs", "careers", "job", "career", "board", "boards", "embed", "c", ""] or not site:
            site = company.get("id", "").lower().replace("-india", "").replace("_india", "").strip()

        if not site:
            return ([], "Lever API: Could not resolve site token") if return_error else []

        api_url = f"https://api.lever.co/v0/postings/{site}?mode=json"
        now_iso = datetime.now().isoformat()
        jobs = []
        error_msg = None

        try:
            r = requests.get(api_url, timeout=8)
            if r is not None and r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    for item in data:
                        title = item.get("text", "")
                        cats = item.get("categories", {})
                        loc = cats.get("location") or "India"
                        full_url = item.get("hostedUrl") or url

                        desc_text = item.get("descriptionPlain") or ""
                        if not desc_text and item.get("description"):
                            from bs4 import BeautifulSoup
                            desc_text = BeautifulSoup(item.get("description"), "html.parser").get_text(separator=" ")

                        lists = item.get("lists", [])
                        if isinstance(lists, list):
                            for lst in lists:
                                lst_title = lst.get("text", "")
                                lst_content = lst.get("content", "")
                                if isinstance(lst_content, list):
                                    lst_content = "\n".join(lst_content)
                                if lst_title or lst_content:
                                    desc_text += f"\n\n{lst_title}:\n{lst_content}"

                        desc_text = str(desc_text).strip()
                        if not desc_text:
                            desc_text = f"Lever posting: {title}"

                        job_id = self._generate_job_id(company["id"], title, full_url)
                        cand_job = {
                            "id": job_id,
                            "company": company["name"],
                            "title": title,
                            "location": loc,
                            "url": full_url,
                            "description": desc_text,
                            "posted_date": None,
                            "extraction_method": "lever_api",
                            "scan_timestamp": now_iso,
                            "first_seen_at": now_iso,
                            "closed": False,
                            "needs_manual_link_review": False,
                            "match": None
                        }
                        is_valid, _ = check_job_posting_validity(cand_job)
                        if is_valid:
                            jobs.append(cand_job)
            elif r is not None and r.status_code == 404:
                disc = self._discover_ats_token(url)
                disc_site = disc.get("lever")
                if disc_site and disc_site != site:
                    disc_api_url = f"https://api.lever.co/v0/postings/{disc_site}?mode=json"
                    r_disc = requests.get(disc_api_url, timeout=8)
                    if r_disc is not None and r_disc.status_code == 200:
                        data = r_disc.json()
                        if isinstance(data, list):
                            for item in data:
                                title = item.get("text", "")
                                cats = item.get("categories", {})
                                loc = cats.get("location") or "India"
                                full_url = item.get("hostedUrl") or url
                                desc_text = item.get("descriptionPlain") or f"Lever posting: {title}"
                                job_id = self._generate_job_id(company["id"], title, full_url)
                                cand_job = {
                                    "id": job_id,
                                    "company": company["name"],
                                    "title": title,
                                    "location": loc,
                                    "url": full_url,
                                    "description": desc_text,
                                    "posted_date": None,
                                    "extraction_method": "lever_api",
                                    "scan_timestamp": now_iso,
                                    "first_seen_at": now_iso,
                                    "closed": False,
                                    "needs_manual_link_review": False,
                                    "match": None
                                }
                                is_valid, _ = check_job_posting_validity(cand_job)
                                if is_valid:
                                    jobs.append(cand_job)
                            return (jobs, None) if return_error else jobs
                error_msg = f"Lever API HTTP 404 for site '{site}'"
            elif r is not None:
                error_msg = f"Lever API HTTP {r.status_code}"
        except Exception as e:
            error_msg = f"Lever API extraction error: {e}"

        if return_error:
            return jobs, error_msg
        return jobs

    def _extract_ashby_jobs(self, company: dict, target_url: str = None, return_error: bool = False):
        url = target_url or company.get("career_url", "")
        import requests

        clean_url = url.rstrip("/")
        from urllib.parse import urlparse
        path = urlparse(clean_url).path
        prefixes = {"v0", "postings", "careers", "embed", "c", "boards", "job_board", "job-board", "job"}
        segments = [s for s in path.split("/") if s and s not in prefixes]
        if segments:
            board = segments[0]
        else:
            board = ""
        if board in ["jobs", "careers", "job", "career", "board", "boards", "embed", "c", ""] or not board:
            board = company.get("id", "").lower().replace("-india", "").replace("_india", "").strip()
        if not board:
            return ([], "Ashby API: Could not resolve board token") if return_error else []
        api_url = f"https://api.ashbyhq.com/posting-api/job-board/{board}"
        now_iso = datetime.now().isoformat()
        jobs = []
        error_msg = None

        try:
            r = requests.get(api_url, timeout=8)
            if r is not None and r.status_code == 200:
                data = r.json()
                for item in data.get("jobs", []):
                    title = item.get("title", "")
                    loc = item.get("locationName") or "India"
                    full_url = item.get("jobUrl") or url

                    desc_html = item.get("descriptionHtml") or item.get("description") or ""
                    if desc_html:
                        from bs4 import BeautifulSoup
                        desc = BeautifulSoup(desc_html, "html.parser").get_text(separator=" ")
                    else:
                        desc = f"Ashby posting: {title}"

                    job_id = self._generate_job_id(company["id"], title, full_url)
                    cand_job = {
                        "id": job_id,
                        "company": company["name"],
                        "title": title,
                        "location": loc,
                        "url": full_url,
                        "description": desc,
                        "posted_date": item.get("publishedAt"),
                        "extraction_method": "ashby_api",
                        "scan_timestamp": now_iso,
                        "first_seen_at": now_iso,
                        "closed": False,
                        "needs_manual_link_review": False,
                        "match": None
                    }
                    is_valid, _ = check_job_posting_validity(cand_job)
                    if is_valid:
                        jobs.append(cand_job)
            elif r is not None and r.status_code == 404:
                disc = self._discover_ats_token(url)
                disc_board = disc.get("ashby")
                if disc_board and disc_board != board:
                    disc_api_url = f"https://api.ashbyhq.com/posting-api/job-board/{disc_board}"
                    r_disc = requests.get(disc_api_url, timeout=8)
                    if r_disc is not None and r_disc.status_code == 200:
                        data = r_disc.json()
                        for item in data.get("jobs", []):
                            title = item.get("title", "")
                            loc = item.get("locationName") or "India"
                            full_url = item.get("jobUrl") or url
                            desc_html = item.get("descriptionHtml") or item.get("description") or f"Ashby posting: {title}"
                            job_id = self._generate_job_id(company["id"], title, full_url)
                            cand_job = {
                                "id": job_id,
                                "company": company["name"],
                                "title": title,
                                "location": loc,
                                "url": full_url,
                                "description": desc_html,
                                "posted_date": item.get("publishedAt"),
                                "extraction_method": "ashby_api",
                                "scan_timestamp": now_iso,
                                "first_seen_at": now_iso,
                                "closed": False,
                                "needs_manual_link_review": False,
                                "match": None
                            }
                            is_valid, _ = check_job_posting_validity(cand_job)
                            if is_valid:
                                jobs.append(cand_job)
                        return (jobs, None) if return_error else jobs
                error_msg = f"Ashby API HTTP 404 for board '{board}'"
            elif r is not None:
                error_msg = f"Ashby API HTTP {r.status_code}"
        except Exception as e:
            error_msg = f"Ashby API extraction error: {e}"

        if return_error:
            return jobs, error_msg
        return jobs

    def _extract_smartrecruiters_jobs(self, company: dict, target_url: str = None, return_error: bool = False):
        url = target_url or company.get("career_url", "")
        import requests

        clean_url = url.rstrip("/")
        from urllib.parse import urlparse
        path = urlparse(clean_url).path
        prefixes = {"v0", "postings", "careers", "embed", "c", "boards", "job_board", "job-board", "job"}
        segments = [s for s in path.split("/") if s and s not in prefixes]
        if segments:
            comp_id = segments[0]
        else:
            comp_id = ""
        if comp_id in ["jobs", "careers", "job", "career", "board", "boards", "embed", "c", ""] or not comp_id:
            comp_id = company.get("id", "").lower().replace("-india", "").replace("_india", "").strip()
        if not comp_id:
            return ([], "SmartRecruiters API: Could not resolve company ID") if return_error else []

        api_url = f"https://api.smartrecruiters.com/v1/companies/{comp_id}/postings"
        now_iso = datetime.now().isoformat()
        jobs = []
        error_msg = None

        try:
            r = requests.get(api_url, timeout=8)
            if r is not None and r.status_code == 200:
                data = r.json()
                for item in data.get("content", []):
                    title = item.get("name", "")
                    loc_info = item.get("location", {})
                    loc = loc_info.get("city") or loc_info.get("country") or "India"
                    full_url = f"https://jobs.smartrecruiters.com/{comp_id}/{item.get('id')}"

                    post_id = item.get("id")
                    detail_url = f"https://api.smartrecruiters.com/v1/companies/{comp_id}/postings/{post_id}"
                    desc_text = ""
                    try:
                        detail_r = requests.get(detail_url, timeout=4)
                        if detail_r.status_code == 200:
                            detail_data = detail_r.json()
                            job_ad = detail_data.get("jobAd", {})
                            sections = job_ad.get("sections", {})
                            parts = []
                            for sec_name in ["companyDescription", "jobDescription", "qualifications", "additionalInformation"]:
                                sec = sections.get(sec_name, {})
                                if isinstance(sec, dict) and sec.get("text"):
                                    parts.append(sec.get("text"))
                            desc_text = "\n\n".join(parts)
                    except Exception:
                        pass

                    if desc_text:
                        from bs4 import BeautifulSoup
                        desc = BeautifulSoup(desc_text, "html.parser").get_text(separator=" ")
                    else:
                        desc = f"SmartRecruiters posting: {title}"

                    job_id = self._generate_job_id(company["id"], title, full_url)
                    cand_job = {
                        "id": job_id,
                        "company": company["name"],
                        "title": title,
                        "location": loc,
                        "url": full_url,
                        "description": desc,
                        "posted_date": item.get("releasedDate"),
                        "extraction_method": "smartrecruiters_api",
                        "scan_timestamp": now_iso,
                        "first_seen_at": now_iso,
                        "closed": False,
                        "needs_manual_link_review": False,
                        "match": None
                    }
                    is_valid, _ = check_job_posting_validity(cand_job)
                    if is_valid:
                        jobs.append(cand_job)
            elif r.status_code == 404:
                error_msg = f"SmartRecruiters API HTTP 404 for company '{comp_id}'"
            else:
                error_msg = f"SmartRecruiters API HTTP {r.status_code}"
        except Exception as e:
            error_msg = f"SmartRecruiters API extraction error: {e}"

        if return_error:
            return jobs, error_msg
        return jobs
