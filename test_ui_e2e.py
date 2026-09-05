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
        check("jobs list renders rows", n_jobs > 0, f"got {n_jobs}")
        badge = page.locator("#badge-jobs").inner_text()
        check("jobs badge shows count", badge.isdigit() and int(badge) == n_jobs, badge)

        # scores sorted descending?
        scores = [int(x) for x in page.locator("#jobs-list .score-ring .val").all_inner_texts()[:10] if x.strip().isdigit()]
        check("jobs sorted by match desc", scores == sorted(scores, reverse=True), str(scores))

        # Jobs tab shows only the last 24 hours
        times = page.locator("#jobs-list .job-meta-right .time").all_inner_texts()
        stale = [t for t in times if "d ago" in t and t.split("d")[0].strip().isdigit() and int(t.split("d")[0].strip()) >= 2]
        check("jobs tab is 24h-fresh", not stale, str(stale[:3]))

        # REGRESSION (user report): numbered senior roles must never appear.
        import re as _re
        titles = page.locator("#jobs-list .job-title-line .t").all_inner_texts()
        leveled = [t for t in titles if _re.search(r"\b(sde|swe|engineer|developer)\s*[-‐–—]?\s*(2|3|4|ii|iii|iv)\b", t.lower())]
        check("no SDE-2/3 style senior roles in feed", not leveled, str(leveled[:3]))

        # jobs sort control works
        page.locator('#jobs-sort button[data-s="recent"]').click()
        page.wait_for_timeout(400)
        check("jobs sort control switches", "on" in (page.locator('#jobs-sort button[data-s="recent"]').get_attribute("class") or ""))
        page.locator('#jobs-sort button[data-s="verified"]').click()
        page.wait_for_timeout(400)
        badges = page.locator("#jobs-list .job-row .badge.llm").count()
        first_badge = page.locator("#jobs-list .job-row .badge").first.inner_text()
        check("verified-first sort puts AI jobs on top",
              badges == 0 or "AI" in first_badge, f"{badges} AI jobs, first={first_badge}")
        page.locator('#jobs-sort button[data-s="match"]').click()
        page.wait_for_timeout(400)

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
        # major cities pinned in the location filter
        loc_chips = page.locator("#f-location .f-chip").all_inner_texts()
        check("major cities pinned", any("Bangalore" in c for c in loc_chips) and any("Mumbai" in c for c in loc_chips), str(loc_chips[:5]))
        check("no bengaluru/bangalore split", not any("Bengaluru" in c for c in loc_chips), str(loc_chips[:9]))
        # min-match slider to 75
        page.locator("#f-score-slider").evaluate(
            "el => { el.value = 75; el.dispatchEvent(new Event('input', {bubbles: true})); }")
        page.wait_for_timeout(400)
        check("score bubble updates", "75" in page.locator("#score-bubble").inner_text())
        n_after = page.locator("#search-list .job-row").count()
        check("score slider filters list", n_after <= n_before, f"{n_before} -> {n_after}")
        # sort by best fit
        page.locator('#search-sort button[data-s="match"]').click()
        page.wait_for_timeout(400)
        s2 = [int(x) for x in page.locator("#search-list .score-ring .val").all_inner_texts()[:8] if x.strip().isdigit()]
        check("best-fit sort works", s2 == sorted(s2, reverse=True), str(s2))
        check("filtered jobs all >= 75", all(s >= 75 for s in s2), str(s2))
        # reset slider
        page.locator("#f-score-slider").evaluate(
            "el => { el.value = 0; el.dispatchEvent(new Event('input', {bubbles: true})); }")

        # ---- tracker: apply-later round trip ----
        page.locator('.nav-item[data-tab="jobs"]').click()
        page.wait_for_timeout(1200)  # let loadJobs re-render fully (cached api is fast)
        # expand the first row; retry once if a re-render collapsed it
        for _attempt in range(3):
            row = page.locator("#jobs-list .job-row").first
            row.locator(".job-head").click()
            page.wait_for_timeout(600)
            row = page.locator("#jobs-list .job-row").first
            if "open" in (row.get_attribute("class") or ""):
                break
        check("row re-expanded for tracker test", "open" in (row.get_attribute("class") or ""))
        job_id = row.get_attribute("data-id")
        row.locator("button:has-text('Apply later')").click(timeout=8000)
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
        page.wait_for_selector("#tracker-list .card, #tracker-list .t-empty, #tracker-list .job-row", timeout=15000)
        body = page.locator("#tracker-list").inner_text()
        check("applied tab renders", len(body.strip()) > 0)

        # ---- insights ----
        page.locator('.nav-item[data-tab="insights"]').click()
        page.wait_for_timeout(1400)
        check("insights stat cards", page.locator("#insights-body .stat-card").count() >= 4)
        first_num = page.locator("#insights-body .num").first.inner_text().replace(",", "")
        check("counter animated to real value", first_num.isdigit() and int(first_num) > 1000, first_num)
        check("funnel bars render", page.locator("#insights-body .bar-row").count() >= 4)
        check("75% goal gauge renders", page.locator("#insights-body .gauge").count() == 1)
        check("gauge shows target", "75" in page.locator("#insights-body .g-val").inner_text())
        check("activity feed present", "scout" in page.locator("#insights-body").inner_text().lower())

        # ---- settings ----
        page.locator('.nav-item[data-tab="settings"]').click()
        page.wait_for_timeout(1000)
        check("settings rows render", page.locator("#settings-body .set-row").count() >= 5)
        check("bg-search toggle present", page.locator("#set-bg-enabled").count() == 1)
        check("interval picker present", page.locator("#set-interval button").count() == 3)
        check("roles editor present", page.locator("#set-roles").count() == 1)
        check("companies stats render", "tracked" in page.locator("#settings-companies").inner_text())
        check("75% goal in settings", "75" in page.locator("#settings-companies").inner_text())

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
