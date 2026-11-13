"""
Score the rolling z-score detector against the simulator's injected faults.

Run: python -m tests.evaluate_anomaly [--seed N]
The simulator's `is_fault` flag is used only as the answer key here; the
detector itself sees measurements only.
"""

import argparse
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.app.anomaly import RollingZScoreDetector
from backend.app.models import ReadingMessage
from simulator.simulation import simulate_readings

METERS = 5
STEPS = 1440 * 3  # three simulated days at one-minute intervals
SEED = 2026
START = datetime(2026, 6, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Score:
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int

    @property
    def precision(self) -> float:
        flagged = self.true_positives + self.false_positives
        return self.true_positives / flagged if flagged else 0.0

    @property
    def recall(self) -> float:
        actual = self.true_positives + self.false_negatives
        return self.true_positives / actual if actual else 0.0

    @property
    def false_positive_rate(self) -> float:
        normal = self.false_positives + self.true_negatives
        return self.false_positives / normal if normal else 0.0


def score(detector: RollingZScoreDetector, readings: list[ReadingMessage]) -> Score:
    tp = fp = fn = tn = 0
    for reading in readings:
        flagged = detector.observe(reading) is not None
        if flagged and reading.is_fault:
            tp += 1
        elif flagged:
            fp += 1
        elif reading.is_fault:
            fn += 1
        else:
            tn += 1
    return Score(tp, fp, fn, tn)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=SEED, help=f"simulation seed (default {SEED})")
    seed = parser.parse_args().seed
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
            [f"meter-{n:03d}" for n in range(1, METERS + 1)],
            START,
            STEPS,
            timedelta(minutes=1),
            random.Random(seed),
        )
    ]
    faults = sum(r.is_fault for r in readings)
    print(f"{len(readings)} readings, {faults} injected faults (seed {seed})\n")
    print(f"{'threshold':>9} {'baseline':>16} {'TP':>4} {'FP':>4} {'FN':>4} "
          f"{'precision':>9} {'recall':>7} {'FP rate':>8}")
    for policy in ("clip", "exclude", "include"):
        for threshold in (2.5, 3.0, 3.5, 4.0):
            result = score(
                RollingZScoreDetector(threshold=threshold, baseline_policy=policy),
                readings,
            )
            print(
                f"{threshold:>9.1f} {policy:>16} "
                f"{result.true_positives:>4} {result.false_positives:>4} "
                f"{result.false_negatives:>4} {result.precision:>9.3f} "
                f"{result.recall:>7.3f} {result.false_positive_rate:>8.4f}"
            )


if __name__ == "__main__":
    main()
