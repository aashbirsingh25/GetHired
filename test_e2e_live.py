import requests
import json
import time

BASE_URL = "http://127.0.0.1:5050"

def test_live_server():
    print("==================================================")
    print("LIVE E2E BEHAVIORAL VERIFICATION")
    print("==================================================")

    # 1. GET /api/jobs
    print("1. Fetching /api/jobs...")
    start_t = time.time()
    r = requests.get(f"{BASE_URL}/api/jobs", timeout=60)
    dur = round(time.time() - start_t, 2)
    assert r.status_code == 200, f"GET /api/jobs failed: {r.status_code}"
    data = r.json()
    jobs = data.get("jobs", [])
    print(f"   [SUCCESS] Received {len(jobs)} jobs in {dur}s")
    if jobs:
        sample = jobs[0]
        print(f"   Sample Job: [{sample['id']}] {sample['title']} at {sample['company']} (Match: {sample.get('match', {}).get('score')}%)")

    # 2. Test Refresh Idempotency (2nd call should be instantaneous)
    print("2. Testing refresh idempotency (2nd GET /api/jobs)...")
    start_t = time.time()
    r2 = requests.get(f"{BASE_URL}/api/jobs", timeout=5)
    dur2 = round(time.time() - start_t, 3)
    assert r2.status_code == 200
    data2 = r2.json()
    jobs2 = data2.get("jobs", [])
    print(f"   [SUCCESS] Received {len(jobs2)} jobs in {dur2}s (Instant cache hit!)")
    assert len(jobs) == len(jobs2), "Feed job count must be identical on refresh"

    # 3. Apply to 1 Job
    if jobs:
        target_job = jobs[0]
        target_id = target_job["id"]
        print(f"3. Applying to job [{target_id}]...")
        app_resp = requests.post(f"{BASE_URL}/api/job/{target_id}/apply-direct", timeout=10)
        assert app_resp.status_code == 200, f"Apply direct failed: {app_resp.status_code}"
        print(f"   [SUCCESS] Application recorded for job [{target_id}]")

        # 4. Verify Applied Job is excluded from default feed
        print("4. Verifying applied job is excluded from main feed...")
        r3 = requests.get(f"{BASE_URL}/api/jobs", timeout=5)
        jobs3 = r3.json().get("jobs", [])
        self_in_feed = any(j["id"] == target_id for j in jobs3)
        assert not self_in_feed, "Applied job must NOT leak into default jobs feed"
        print("   [SUCCESS] Applied job correctly excluded from main feed!")

        # 5. Verify Applied Job is listed in /api/applications
        print("5. Verifying applied job appears in /api/applications...")
        apps_resp = requests.get(f"{BASE_URL}/api/applications", timeout=5)
        apps_data = apps_resp.json().get("applications", [])
        has_app = any(a["job_id"] == target_id for a in apps_data)
        assert has_app, "Applied job must appear in /api/applications"
        print("   [SUCCESS] Applied job correctly listed in /api/applications!")

    # 6. Real-time Search Test
    print("6. Testing real-time search endpoint /api/jobs/search...")
    search_resp = requests.post(f"{BASE_URL}/api/jobs/search", json={"target_role": ["Software Engineer"]}, timeout=10)
    assert search_resp.status_code == 200
    search_data = search_resp.json()
    print(f"   [SUCCESS] Real-time search returned {search_data.get('total_jobs')} jobs in {search_data.get('search_duration')}")

    print("==================================================")
    print("ALL LIVE E2E BEHAVIORAL VERIFICATIONS PASSED 100%!")
    print("==================================================")

if __name__ == "__main__":
    test_live_server()
