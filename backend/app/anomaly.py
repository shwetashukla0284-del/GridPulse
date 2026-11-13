from collections import deque
from statistics import fmean, stdev
from typing import Literal

from backend.app.models import AnomalyAlert, ReadingMessage


# How a flagged value enters the baseline window:
# - "clip": add it capped at mean +/- threshold * std (default). The baseline can
#   still follow genuine trends, while one surge moves it only slightly.
# - "exclude": leave it out. Measured to fail on trends: during the night-time
#   demand decline, low values are flagged and dropped, so the baseline stays
#   too high and keeps flagging ordinary readings.
# - "include": add it unchanged. Surges inflate mean/std, hiding later surges.
BaselinePolicy = Literal["clip", "exclude", "include"]


class RollingZScoreDetector:
    """
    Flag demand readings far from each meter's recent behaviour.

    For every meter, keep the last `window_size` demand values. A new reading's
    z-score is (value - mean) / std of that window. The detector judges only
    measurements; it never looks at the simulator's `is_fault` ground truth.
    """

    def __init__(
        self,
        window_size: int = 30,
        # 4.0 chosen on seed 2026 and confirmed on held-out seeds 7 and 99
        # (precision 0.953 / 0.913, recall 1.000 / 0.995).
        threshold: float = 4.0,
        min_samples: int = 10,
        min_std_kw: float = 0.05,
        baseline_policy: BaselinePolicy = "clip",
    ) -> None:
        if window_size < 2:
            raise ValueError("window_size must be at least 2")
        if not 2 <= min_samples <= window_size:
            raise ValueError("min_samples must be between 2 and window_size")
        if threshold <= 0:
            raise ValueError("threshold must be positive")
        if min_std_kw <= 0:
            raise ValueError("min_std_kw must be positive")

        self._window_size = window_size
        self._threshold = threshold
        self._min_samples = min_samples
        self._min_std_kw = min_std_kw
        self._baseline_policy = baseline_policy
        self._windows: dict[str, deque[float]] = {}

    def observe(self, reading: ReadingMessage) -> AnomalyAlert | None:
        window = self._windows.setdefault(
            reading.meter_id, deque(maxlen=self._window_size)
        )

        anomaly: AnomalyAlert | None = None
        value_for_baseline = reading.demand_kw
        if len(window) >= self._min_samples:
            mean = fmean(window)
            # The floor stops a nearly flat window from turning tiny noise
            # into a huge z-score.
            std = max(stdev(window), self._min_std_kw)
            z_score = (reading.demand_kw - mean) / std
            if abs(z_score) > self._threshold:
                anomaly = AnomalyAlert(
                    meter_id=reading.meter_id,
                    timestamp=reading.timestamp,
                    demand_kw=reading.demand_kw,
                    baseline_mean_kw=mean,
                    baseline_std_kw=std,
                    z_score=z_score,
                )
                limit = self._threshold * std
                value_for_baseline = min(max(reading.demand_kw, mean - limit), mean + limit)

        if anomaly is None or self._baseline_policy == "include":
            window.append(reading.demand_kw)
        elif self._baseline_policy == "clip":
            window.append(value_for_baseline)
        return anomaly
