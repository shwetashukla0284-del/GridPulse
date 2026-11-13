import random
from datetime import datetime

from simulator.profiles import estimate_demand_kw, estimate_solar_kw
from simulator.reading import MeterReading


def generate_reading(
    meter_id: str,
    timestamp: datetime,
    rng: random.Random,
    *,
    solar_capacity_kw: float = 5.0,
    demand_noise_std_kw: float = 0.08,
    solar_noise_std_kw: float = 0.12,
    fault_probability: float = 0.01,
) -> MeterReading:
    """Generate one noisy reading and occasionally mark an injected demand surge."""
    if demand_noise_std_kw < 0:
        raise ValueError("demand_noise_std_kw cannot be negative")
    if solar_noise_std_kw < 0:
        raise ValueError("solar_noise_std_kw cannot be negative")
    if not 0.0 <= fault_probability <= 1.0:
        raise ValueError("fault_probability must be between 0 and 1")

    demand_kw = max(
        0.0,
        estimate_demand_kw(timestamp) + rng.gauss(0.0, demand_noise_std_kw),
    )
    solar_kw = max(
        0.0,
        estimate_solar_kw(timestamp, solar_capacity_kw)
        + rng.gauss(0.0, solar_noise_std_kw),
    )

    is_fault = rng.random() < fault_probability
    if is_fault:
        demand_kw += rng.uniform(3.0, 8.0)

    return MeterReading(
        meter_id=meter_id,
        timestamp=timestamp,
        demand_kw=demand_kw,
        solar_kw=solar_kw,
        is_fault=is_fault,
    )
