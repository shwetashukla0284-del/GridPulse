import random
import unittest
from datetime import datetime, timedelta, timezone

from backend.app.anomaly import RollingZScoreDetector
from backend.app.models import ReadingMessage
from simulator.simulation import simulate_readings

START = datetime(2026, 6, 1, tzinfo=timezone.utc)


def reading(meter_id: str, minute: int, demand_kw: float) -> ReadingMessage:
    return ReadingMessage(
        meter_id=meter_id,
        timestamp=START + timedelta(minutes=minute),
        demand_kw=demand_kw,
        solar_kw=0.0,
        net_grid_kw=demand_kw,
        is_fault=False,
    )


# Small alternating wobble around 1 kW: mean 1.0, sample std about 0.105.
NORMAL = [1.1 if i % 2 else 0.9 for i in range(20)]


class RollingZScoreDetectorTests(unittest.TestCase):
    def test_flags_a_surge_after_warm_up(self) -> None:
        detector = RollingZScoreDetector(window_size=20, min_samples=10)
        for minute, value in enumerate(NORMAL):
            self.assertIsNone(detector.observe(reading("m1", minute, value)))

        anomaly = detector.observe(reading("m1", 20, 6.0))

        self.assertIsNotNone(anomaly)
        assert anomaly is not None
        self.assertAlmostEqual(anomaly.baseline_mean_kw, 1.0)
        self.assertGreater(anomaly.z_score, 3.0)

    def test_does_not_flag_during_warm_up(self) -> None:
        detector = RollingZScoreDetector(min_samples=10)
        for minute in range(9):
            detector.observe(reading("m1", minute, 1.0))
        self.assertIsNone(detector.observe(reading("m1", 9, 50.0)))

    def test_meters_have_independent_baselines(self) -> None:
        detector = RollingZScoreDetector(window_size=20, min_samples=10)
        for minute, value in enumerate(NORMAL):
            detector.observe(reading("small", minute, value))
            detector.observe(reading("large", minute, value + 5.0))

        # 6 kW is normal for the large meter but a surge for the small one.
        self.assertIsNone(detector.observe(reading("large", 20, 6.0)))
        self.assertIsNotNone(detector.observe(reading("small", 20, 6.0)))

    def test_std_floor_prevents_flagging_tiny_changes_on_flat_data(self) -> None:
        detector = RollingZScoreDetector(min_samples=10, min_std_kw=0.05)
        for minute in range(10):
            detector.observe(reading("m1", minute, 1.0))
        # Without the floor std is 0 and any change is an infinite z-score.
        self.assertIsNone(detector.observe(reading("m1", 10, 1.1)))

    def test_flagged_values_do_not_mask_the_next_surge(self) -> None:
        excluding = RollingZScoreDetector(
            window_size=20, min_samples=10, baseline_policy="exclude"
        )
        including = RollingZScoreDetector(
            window_size=20, min_samples=10, baseline_policy="include"
        )
        for detector in (excluding, including):
            for minute, value in enumerate(NORMAL):
                detector.observe(reading("m1", minute, value))
            self.assertIsNotNone(detector.observe(reading("m1", 20, 6.0)))

        # Absorbed surges inflate the including detector's mean and std: the
        # second surge's z-score drops sharply and the third is missed. A
        # sustained fault would quietly become the new "normal".
        second_excluding = excluding.observe(reading("m1", 21, 6.0))
        second_including = including.observe(reading("m1", 21, 6.0))
        assert second_excluding is not None and second_including is not None
        self.assertLess(second_including.z_score, second_excluding.z_score / 5)
        self.assertIsNotNone(excluding.observe(reading("m1", 22, 6.0)))
        self.assertIsNone(including.observe(reading("m1", 22, 6.0)))

    def test_clipping_keeps_detecting_repeated_surges(self) -> None:
        detector = RollingZScoreDetector(window_size=20, min_samples=10)
        for minute, value in enumerate(NORMAL):
            detector.observe(reading("m1", minute, value))
        for minute in range(20, 25):
            self.assertIsNotNone(detector.observe(reading("m1", minute, 6.0)))

    def test_clipping_lets_the_baseline_follow_the_night_decline(self) -> None:
        # Simulated 20:00-03:00, when demand falls after the evening peak.
        # Excluding flagged lows keeps the baseline too high, so ordinary
        # readings keep getting flagged; clipping lets the baseline follow.
        readings = [
            ReadingMessage(
                meter_id=r.meter_id,
                timestamp=r.timestamp,
                demand_kw=r.demand_kw,
                solar_kw=r.solar_kw,
                net_grid_kw=r.net_grid_kw,
                is_fault=r.is_fault,
            )
            for r in simulate_readings(
                ["m1", "m2", "m3"],
                START.replace(hour=20),
                420,
                timedelta(minutes=1),
                random.Random(1),
            )
        ]
        false_alarms = {}
        for policy in ("clip", "exclude"):
            # Pinned to 3.0, where the exclusion feedback loop shows clearly;
            # the higher default flags fewer ordinary lows to start the loop.
            detector = RollingZScoreDetector(threshold=3.0, baseline_policy=policy)
            false_alarms[policy] = sum(
                detector.observe(r) is not None and not r.is_fault for r in readings
            )
        self.assertLess(false_alarms["clip"] * 5, false_alarms["exclude"])

    def test_rejects_invalid_settings(self) -> None:
        with self.assertRaisesRegex(ValueError, "min_samples"):
            RollingZScoreDetector(window_size=5, min_samples=10)
        with self.assertRaisesRegex(ValueError, "threshold"):
            RollingZScoreDetector(threshold=0)
