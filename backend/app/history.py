from dataclasses import dataclass
from datetime import datetime, timedelta

MIN_BUCKET_SECONDS = 1
MAX_BUCKET_SECONDS = 86_400
MAX_BUCKETS = 2_000
DEFAULT_WINDOW = timedelta(hours=1)


MAX_KPI_RANGE = timedelta(days=31)


@dataclass(frozen=True)
class TimeRange:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class HistoryWindow:
    start: datetime
    end: datetime
    bucket: timedelta


def resolve_time_range(
    start: datetime | None,
    end: datetime | None,
    now: datetime,
    max_range: timedelta | None = None,
) -> TimeRange:
    """Default to the last hour and require an ordered, timezone-aware range."""
    resolved_end = end or now
    resolved_start = start or resolved_end - DEFAULT_WINDOW
    for value in (resolved_start, resolved_end):
        if value.utcoffset() is None:
            raise ValueError("start and end must include a timezone offset")
    if resolved_end <= resolved_start:
        raise ValueError("end must be after start")
    if max_range is not None and resolved_end - resolved_start > max_range:
        raise ValueError(f"range cannot exceed {max_range.days} days")
    return TimeRange(resolved_start, resolved_end)


def resolve_history_window(
    start: datetime | None,
    end: datetime | None,
    bucket_seconds: int,
    now: datetime,
) -> HistoryWindow:
    """Apply defaults and reject windows that are invalid or too large to return."""
    if not MIN_BUCKET_SECONDS <= bucket_seconds <= MAX_BUCKET_SECONDS:
        raise ValueError(
            f"bucket_seconds must be between {MIN_BUCKET_SECONDS} and {MAX_BUCKET_SECONDS}"
        )

    time_range = resolve_time_range(start, end, now)
    bucket = timedelta(seconds=bucket_seconds)
    if (time_range.end - time_range.start) / bucket > MAX_BUCKETS:
        raise ValueError(
            f"window would return more than {MAX_BUCKETS} buckets; use a larger bucket_seconds"
        )
    return HistoryWindow(time_range.start, time_range.end, bucket)


def solar_coverage_pct(demand_kwh: float, import_kwh: float) -> float | None:
    """Share of demand met by on-site solar: (demand - grid import) / demand."""
    if demand_kwh <= 0:
        return None
    return 100 * (demand_kwh - import_kwh) / demand_kwh
