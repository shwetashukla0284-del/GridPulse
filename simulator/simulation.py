import random
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta

from simulator.generator import generate_reading
from simulator.reading import MeterReading


def simulate_readings(
    meter_ids: Sequence[str],
    start_time: datetime,
    steps: int,
    interval: timedelta,
    rng: random.Random,
) -> Iterator[MeterReading]:
    """Yield one reading per meter at each point in virtual time."""
    if not meter_ids or any(not meter_id for meter_id in meter_ids):
        raise ValueError("meter_ids must contain non-empty IDs")
    if len(set(meter_ids)) != len(meter_ids):
        raise ValueError("meter_ids must be unique")
    if start_time.utcoffset() is None:
        raise ValueError("start_time must include a timezone")
    if steps < 1:
        raise ValueError("steps must be at least 1")
    if interval <= timedelta(0):
        raise ValueError("interval must be positive")

    for step in range(steps):
        timestamp = start_time + step * interval
        for meter_id in meter_ids:
            yield generate_reading(meter_id, timestamp, rng)
