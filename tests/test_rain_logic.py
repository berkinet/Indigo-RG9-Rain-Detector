import importlib.util
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "RG-9 Rain Detector.indigoPlugin" / "Contents" / "Server Plugin"
spec = importlib.util.spec_from_file_location("rain_logic", SERVER / "rain_logic.py")
rain_logic = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = rain_logic
spec.loader.exec_module(rain_logic)


class RainStateTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 8, 17, 10, 0, 0)
        self.state = rain_logic.RainState(
            second_detection_seconds=30,
            minimum_high_seconds=30,
            dry_seconds=60,
        )

    def test_isolated_detection_does_not_confirm_rain(self):
        self.assertFalse(self.state.detection(self.start))
        self.state.advance(self.start + timedelta(seconds=31))
        self.assertFalse(self.state.is_raining)
        self.assertIsNone(self.state.candidate_at)

    def test_second_detection_within_window_confirms_from_first(self):
        self.state.detection(self.start)
        self.assertTrue(self.state.detection(self.start + timedelta(seconds=20)))
        self.assertTrue(self.state.is_raining)
        self.assertEqual(self.state.total_seconds(self.start + timedelta(seconds=25)), 20)

    def test_detection_outside_window_starts_new_candidate(self):
        self.state.detection(self.start)
        self.assertFalse(self.state.detection(self.start + timedelta(seconds=31)))
        self.assertFalse(self.state.is_raining)
        self.assertEqual(self.state.candidate_at, self.start + timedelta(seconds=31))

    def test_rain_ends_one_dry_period_after_last_detection(self):
        self.state.detection(self.start)
        self.state.detection(self.start + timedelta(seconds=10))
        self.state.detection(self.start + timedelta(seconds=40))
        self.assertFalse(self.state.advance(self.start + timedelta(seconds=99)))
        self.assertTrue(self.state.advance(self.start + timedelta(seconds=100)))
        self.assertFalse(self.state.is_raining)
        self.assertEqual(self.state.total_seconds(self.start + timedelta(seconds=200)), 40)

    def test_sustained_high_confirms_and_counts_until_falling_edge(self):
        self.assertFalse(self.state.input_changed(True, self.start))
        self.assertFalse(
            self.state.confirm_sustained_high(
                self.start + timedelta(seconds=29)
            )
        )
        self.assertTrue(
            self.state.confirm_sustained_high(
                self.start + timedelta(seconds=30)
            )
        )
        self.assertEqual(
            self.state.total_seconds(self.start + timedelta(seconds=45)), 45
        )
        self.state.input_changed(False, self.start + timedelta(seconds=50))
        self.assertFalse(self.state.advance(self.start + timedelta(seconds=109)))
        self.assertTrue(self.state.advance(self.start + timedelta(seconds=110)))
        self.assertEqual(self.state.rain_ended_at, self.start + timedelta(seconds=50))
        self.assertEqual(self.state.total_seconds(self.start + timedelta(seconds=200)), 50)

    def test_two_pulses_confirm_from_first_rising_edge(self):
        self.state.input_changed(True, self.start)
        self.state.input_changed(False, self.start + timedelta(seconds=2))
        self.assertTrue(
            self.state.input_changed(True, self.start + timedelta(seconds=20))
        )
        self.assertEqual(self.state.detections_today, 2)
        self.assertEqual(self.state.raining_since, self.start)

    def test_second_detection_and_continuous_high_use_independent_timers(self):
        state = rain_logic.RainState(
            second_detection_seconds=60,
            minimum_high_seconds=10,
            dry_seconds=60,
        )
        state.input_changed(True, self.start)
        self.assertTrue(
            state.confirm_sustained_high(self.start + timedelta(seconds=10))
        )

        pulse_state = rain_logic.RainState(
            second_detection_seconds=60,
            minimum_high_seconds=120,
            dry_seconds=60,
        )
        pulse_state.input_changed(True, self.start)
        pulse_state.input_changed(False, self.start + timedelta(seconds=2))
        self.assertTrue(
            pulse_state.input_changed(True, self.start + timedelta(seconds=50))
        )

    def test_daily_total_resets_at_midnight_during_rain(self):
        start = datetime(2026, 8, 17, 23, 59, 30)
        self.state.detection(start)
        self.state.detection(start + timedelta(seconds=10))
        after_midnight = datetime(2026, 8, 18, 0, 0, 20)
        self.assertEqual(self.state.total_seconds(after_midnight), 0)
        self.assertTrue(self.state.is_raining)
        self.assertEqual(self.state.detections_today, 0)

    def test_duration_format(self):
        self.assertEqual(rain_logic.format_duration(3661), "01:01:01")


if __name__ == "__main__":
    unittest.main()
