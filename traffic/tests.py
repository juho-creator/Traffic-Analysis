import unittest
from datetime import datetime

from .count_vehicles import new_crossings, parse_line
from .traffic_report import build_buckets, find_peak, parse_interval, period_name


class ReportTests(unittest.TestCase):
    def test_parse_interval(self):
        self.assertEqual(parse_interval("30s"), 30)
        self.assertEqual(parse_interval("15min"), 900)
        self.assertEqual(parse_interval("1h"), 3600)
        for bad in ("", "0s", "banana", "5 days"):
            with self.assertRaises(ValueError):
                parse_interval(bad)

    def test_period_name(self):
        self.assertEqual(period_name(5), "5-second")
        self.assertEqual(period_name(1800), "30-minute")
        self.assertEqual(period_name(3600), "1-hour")

    def test_buckets_are_clock_aligned_and_mark_partial_periods(self):
        start, end = datetime(2026, 11, 8, 8, 7), datetime(2026, 11, 8, 8, 45)
        vehicles = [(datetime(2026, 11, 8, 8, 20), "car", "in"), (datetime(2026, 11, 8, 8, 25), "truck", "out")]
        buckets = build_buckets(start, end, vehicles, 900)
        self.assertEqual([b.start.minute for b in buckets], [0, 15, 30])
        self.assertEqual([b.partial for b in buckets], [True, False, False])
        self.assertEqual(buckets[1].counts, {"car": 1, "motorcycle": 0, "truck": 1})
        self.assertIs(find_peak(buckets), buckets[1])

    def test_quiet_periods_stay_in_as_zero(self):
        start, end = datetime(2026, 11, 8, 8, 0), datetime(2026, 11, 8, 9, 0)
        buckets = build_buckets(start, end, [], 900)
        self.assertEqual([b.total for b in buckets], [0, 0, 0, 0])
        self.assertIsNone(find_peak(buckets))


class CountingTests(unittest.TestCase):
    def test_parse_line(self):
        self.assertEqual(parse_line("0,0.5,1,0.5", 101, 11), [(0, 5), (100, 5)])
        for bad in ("0,0.5,1", "0,2,1,0.5", "0.5,0.5,0.5,0.5"):
            with self.assertRaises(ValueError):
                parse_line(bad, 100, 100)

    def test_new_crossings_reports_only_new_counts(self):
        seen = {}
        self.assertEqual(list(new_crossings({"car": {"IN": 2, "OUT": 0}}, seen)), [("car", "in"), ("car", "in")])
        self.assertEqual(list(new_crossings({"car": {"IN": 2, "OUT": 1}}, seen)), [("car", "out")])
        self.assertEqual(list(new_crossings({"car": {"IN": 2, "OUT": 1}}, seen)), [])
