"""End-to-end UI test with Playwright against the running app on :5050.

Covers: page load, all six tabs render real content, job row expansion
shows confidence grid + fit box + description, search filters change
counts, tracker apply-later round-trip, settings load.
"""
import os
os.environ.setdefault("OMP_NUM_THREADS", "1")

import sys
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5050"
FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}{(' - ' + detail) if detail and not cond else ''}")
    if not cond:
        FAILURES.append(name)


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # ---- load + jobs tab (default) ----
        page.goto(BASE, timeout=30000)
        page.wait_for_selector(".job-row", timeout=25000)
        check("page loads with sidebar", page.locator("#sidebar").count() == 1)
        n_jobs = page.locator("#jobs-list .job-row").count()
        check("jobs list renders rows", n_jobs > 10, f"got {n_jobs}")
        badge = page.locator("#badge-jobs").inner_text()
        check("jobs badge shows count", badge.isdigit() and int(badge) == n_jobs, badge)

        # scores sorted descending?
        scores = [int(x) for x in page.locator("#jobs-list .score-ring .val").all_inner_texts()[:10] if x.strip().isdigit()]
        check("jobs sorted by match desc", scores == sorted(scores, reverse=True), str(scores))

        # ---- expand first job ----
        page.locator("#jobs-list .job-head").first.click()
        page.wait_for_timeout(600)
        row = page.locator("#jobs-list .job-row").first
        check("row expands", "open" in (row.get_attribute("class") or ""))
        check("confidence grid visible", row.locator(".conf-cell").count() == 3)
        check("why-good-fit shown", row.locator(".fit-box").inner_text().strip() != "")
        check("description shown", len(row.locator(".desc-box").inner_text().strip()) > 20)
        check("apply link present", row.locator("a.btn.primary").count() == 1)

        # ---- search tab ----
        page.locator('.nav-item[data-tab="search"]').click()
        page.wait_for_timeout(700)
        check("search tab visible", page.locator("#tab-search.visible").count() == 1)
        total_txt = page.locator("#search-count").inner_text()
        check("search count shown", "of" in total_txt, total_txt)
        n_before = page.locator("#search-list .job-row").count()
        check("search list renders", n_before > 0, str(n_before))
        # apply min-score filter 75%
        page.locator('#f-score .f-chip[data-v="75"]').click()
        page.wait_for_timeout(400)
        n_after = page.locator("#search-list .job-row").count()
        check("score filter reduces list", n_after < n_before, f"{n_before} -> {n_after}")
        # sort by best fit
        page.locator('#search-sort button[data-s="match"]').click()
        page.wait_for_timeout(400)
        s2 = [int(x) for x in page.locator("#search-list .score-ring .val").all_inner_texts()[:8] if x.strip().isdigit()]
        check("best-fit sort works", s2 == sorted(s2, reverse=True), str(s2))
        check("filtered jobs all >= 75", all(s >= 75 for s in s2), str(s2))
        # reset filter
        page.locator('#f-score .f-chip[data-v="0"]').click()

        # ---- tracker: apply-later round trip ----
        page.locator('.nav-item[data-tab="jobs"]').click()
        page.wait_for_timeout(500)
        row = page.locator("#jobs-list .job-row").first
        if "open" not in (row.get_attribute("class") or ""):
            row.locator(".job-head").click()
            page.wait_for_timeout(500)
        job_id = row.get_attribute("data-id")
        row.locator("button:has-text('Apply later')").click()
        page.wait_for_timeout(600)
        page.locator('.nav-item[data-tab="tracker"]').click()
        page.wait_for_timeout(500)
        page.locator('#tracker-tabs button[data-t="apply_later"]').click()
        page.wait_for_timeout(800)
        later_rows = page.locator("#tracker-list .job-row").count()
        check("apply-later shows the job", later_rows >= 1, str(later_rows))
        found = page.locator(f'#tracker-list .job-row[data-id="{job_id}"]').count()
        check("apply-later has the right job", found == 1)
        # toggle back off via the row's button
        r2 = page.locator(f'#tracker-list .job-row[data-id="{job_id}"]')
        r2.locator(".job-head").click()
        page.wait_for_timeout(400)
        r2.locator("button:has-text('Apply later')").click()
        page.wait_for_timeout(500)

        # tracker applied tab renders (may be empty)
        page.locator('#tracker-tabs button[data-t="applied"]').click()
        page.wait_for_timeout(600)
        body = page.locator("#tracker-list").inner_text()
        check("applied tab renders", len(body.strip()) > 0)

        # ---- insights ----
        page.locator('.nav-item[data-tab="insights"]').click()
        page.wait_for_timeout(1400)
        check("insights stat cards", page.locator("#insights-body .stat-card").count() >= 4)
        first_num = page.locator("#insights-body .num").first.inner_text().replace(",", "")
        check("counter animated to real value", first_num.isdigit() and int(first_num) > 1000, first_num)
        check("funnel bars render", page.locator("#insights-body .bar-row").count() >= 4)

        # ---- settings ----
        page.locator('.nav-item[data-tab="settings"]').click()
        page.wait_for_timeout(900)
        check("settings rows render", page.locator("#settings-body .set-row").count() >= 3)
        check("companies stats render", "tracked" in page.locator("#settings-companies").inner_text())

        # ---- profile ----
        page.locator('.nav-item[data-tab="profile"]').click()
        page.wait_for_timeout(800)
        check("profile drop zone", page.locator("#drop-zone").count() == 1)
        check("resume info loaded", "Resume" in page.locator("#resume-info").inner_text()
              or page.locator("#resume-info .skill-chip").count() > 0)

        # ---- no JS errors anywhere ----
        check("zero JS page errors", len(errors) == 0, "; ".join(errors[:3]))

        browser.close()

    print(f"\n{'ALL PASS' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    run()
