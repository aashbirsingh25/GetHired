"""Scoped tests for scan_scheduler (active/dormant tiering)."""
import unittest
from datetime import datetime, timedelta

from scan_scheduler import (
    classify_company,
    partition_companies,
    update_yield_streak,
    next_cycle_number,
    get_tiering_config,
)

CFG = get_tiering_config({})  # defaults: 3 failures, 7 days, every 4th cycle


class TestScanScheduler(unittest.TestCase):
    def test_never_scanned_is_active(self):
        self.assertEqual(classify_company(None, CFG), "active")
        self.assertEqual(classify_company({}, CFG), "active")

    def test_short_streak_is_active(self):
        self.assertEqual(classify_company({"zero_yield_streak": 2}, CFG), "active")

    def test_long_streak_no_success_is_dormant(self):
        self.assertEqual(classify_company({"zero_yield_streak": 3}, CFG), "dormant")

    def test_recent_success_keeps_active_despite_streak(self):
        recent = (datetime.now() - timedelta(days=2)).isoformat()
        m = {"zero_yield_streak": 5, "last_success_at": recent}
        self.assertEqual(classify_company(m, CFG), "active")

    def test_old_success_goes_dormant(self):
        old = (datetime.now() - timedelta(days=30)).isoformat()
        m = {"zero_yield_streak": 5, "last_success_at": old}
        self.assertEqual(classify_company(m, CFG), "dormant")

    def test_partition_skips_dormant_except_every_nth_cycle(self):
        companies = [{"id": "good"}, {"id": "dead"}]
        metrics = {"companies": {
            "good": {"zero_yield_streak": 0},
            "dead": {"zero_yield_streak": 10},
        }}
        # cycle 1: dormant skipped
        to_scan, skipped = partition_companies(companies, metrics, {}, 1)
        self.assertEqual([c["id"] for c in to_scan], ["good"])
        self.assertEqual([c["id"] for c in skipped], ["dead"])
        # cycle 4 (multiple of default 4): dormant included
        to_scan, skipped = partition_companies(companies, metrics, {}, 4)
        self.assertEqual([c["id"] for c in to_scan], ["good", "dead"])
        self.assertEqual(skipped, [])

    def test_partition_disabled_scans_everything(self):
        companies = [{"id": "dead"}]
        metrics = {"companies": {"dead": {"zero_yield_streak": 10}}}
        to_scan, skipped = partition_companies(
            companies, metrics, {"scan_tiering": {"enabled": False}}, 1)
        self.assertEqual(len(to_scan), 1)
        self.assertEqual(skipped, [])

    def test_yield_streak_updates(self):
        m = {}
        update_yield_streak(m, 0, "2026-08-23T02:00:00")
        update_yield_streak(m, 0, "2026-08-23T02:00:00")
        self.assertEqual(m["zero_yield_streak"], 2)
        update_yield_streak(m, 5, "2026-08-23T02:05:00")
        self.assertEqual(m["zero_yield_streak"], 0)
        self.assertEqual(m["last_success_at"], "2026-08-23T02:05:00")

    def test_cycle_counter_increments(self):
        store = {}
        self.assertEqual(next_cycle_number(store), 1)
        self.assertEqual(next_cycle_number(store), 2)
        self.assertEqual(store["scheduler"]["cycle_number"], 2)


if __name__ == "__main__":
    unittest.main()


class TestFresherTiering(unittest.TestCase):
    """Fresher-aware slowdown must never drop a company and never skip it twice
    in a row (it can post a fresher role any day)."""

    def _companies(self):
        return [{"id": "posts_fresher"}, {"id": "no_fresher"}]

    def _metrics(self):
        return {"companies": {
            "posts_fresher": {"zero_yield_streak": 0, "jobs_extracted": 50, "fresher_zero_streak": 0},
            "no_fresher": {"zero_yield_streak": 0, "jobs_extracted": 200, "fresher_zero_streak": 30},
        }}

    def test_fresher_dry_company_is_slowed_not_dropped(self):
        odd, _ = partition_companies(self._companies(), self._metrics(), {}, 1)
        even, _ = partition_companies(self._companies(), self._metrics(), {}, 2)
        odd_ids = [c["id"] for c in odd]
        even_ids = [c["id"] for c in even]
        # skipped on odd cycles, scanned on even ones -> at most one cycle gap
        self.assertNotIn("no_fresher", odd_ids)
        self.assertIn("no_fresher", even_ids)
        # a company that does post fresher roles is never slowed
        self.assertIn("posts_fresher", odd_ids)
        self.assertIn("posts_fresher", even_ids)

    def test_fresher_tiering_can_be_disabled(self):
        cfg = {"scan_tiering": {"fresher_tiering_enabled": False}}
        got, _ = partition_companies(self._companies(), self._metrics(), cfg, 1)
        self.assertEqual(len(got), 2)

    def test_short_fresher_streak_not_slowed(self):
        metrics = self._metrics()
        metrics["companies"]["no_fresher"]["fresher_zero_streak"] = 3
        got, _ = partition_companies(self._companies(), metrics, {}, 1)
        self.assertIn("no_fresher", [c["id"] for c in got])
