from datetime import datetime
from math import exp, pi, sin


def _hour_of_day(timestamp: datetime) -> float:
    return (
        timestamp.hour
        + timestamp.minute / 60
        + timestamp.second / 3600
        + timestamp.microsecond / 3_600_000_000
    )


def estimate_demand_kw(timestamp: datetime) -> float:
    """Estimate household demand with morning and evening peaks."""
    hour = _hour_of_day(timestamp)
    morning_peak = 1.6 * exp(-0.5 * ((hour - 7.5) / 1.5) ** 2)
    evening_peak = 2.4 * exp(-0.5 * ((hour - 19.0) / 2.5) ** 2)
    return 0.4 + morning_peak + evening_peak


def estimate_solar_kw(timestamp: datetime, capacity_kw: float = 5.0) -> float:
    """Estimate solar output using a smooth daylight-only daily profile."""
    if capacity_kw < 0:
        raise ValueError("capacity_kw cannot be negative")

    hour = _hour_of_day(timestamp)
    sunrise_hour = 6.0
    daylight_hours = 12.0

    if not sunrise_hour < hour < sunrise_hour + daylight_hours:
        return 0.0

    daylight_position = (hour - sunrise_hour) / daylight_hours
    return capacity_kw * sin(pi * daylight_position)
