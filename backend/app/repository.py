from collections.abc import Sequence
from typing import Protocol

import asyncpg

from backend.app.history import HistoryWindow, TimeRange, solar_coverage_pct
from backend.app.models import AnomalyAlert, HistoryBucket, KpiSummary, ReadingMessage

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS meter_readings (
    timestamp TIMESTAMPTZ NOT NULL,
    meter_id TEXT NOT NULL,
    demand_kw DOUBLE PRECISION NOT NULL CHECK (demand_kw >= 0),
    solar_kw DOUBLE PRECISION NOT NULL CHECK (solar_kw >= 0),
    net_grid_kw DOUBLE PRECISION NOT NULL,
    is_fault BOOLEAN NOT NULL,
    PRIMARY KEY (timestamp, meter_id),
    CHECK (abs(net_grid_kw - (demand_kw - solar_kw)) < 0.000000001)
);

SELECT create_hypertable(
    'meter_readings',
    'timestamp',
    if_not_exists => TRUE
);

CREATE TABLE IF NOT EXISTS anomaly_alerts (
    timestamp TIMESTAMPTZ NOT NULL,
    meter_id TEXT NOT NULL,
    demand_kw DOUBLE PRECISION NOT NULL,
    baseline_mean_kw DOUBLE PRECISION NOT NULL,
    baseline_std_kw DOUBLE PRECISION NOT NULL CHECK (baseline_std_kw > 0),
    z_score DOUBLE PRECISION NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (timestamp, meter_id)
);

SELECT create_hypertable(
    'anomaly_alerts',
    'timestamp',
    if_not_exists => TRUE
);
"""

# DO NOTHING keeps the first judgement if a reading is replayed: after a
# restart the detector's in-memory baseline differs, so a replay should not
# overwrite the original alert.
INSERT_ALERT_SQL = """
INSERT INTO anomaly_alerts (
    timestamp, meter_id, demand_kw, baseline_mean_kw, baseline_std_kw, z_score
)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (timestamp, meter_id) DO NOTHING
"""

RECENT_ALERTS_SQL = """
SELECT meter_id, timestamp, demand_kw, baseline_mean_kw, baseline_std_kw, z_score
FROM anomaly_alerts
WHERE timestamp >= $1 AND timestamp < $2
ORDER BY timestamp DESC, meter_id
LIMIT $3
"""

INSERT_SQL = """
INSERT INTO meter_readings (
    timestamp, meter_id, demand_kw, solar_kw, net_grid_kw, is_fault
)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (timestamp, meter_id) DO UPDATE SET
    demand_kw = EXCLUDED.demand_kw,
    solar_kw = EXCLUDED.solar_kw,
    net_grid_kw = EXCLUDED.net_grid_kw,
    is_fault = EXCLUDED.is_fault
"""

LATEST_SQL = """
SELECT DISTINCT ON (meter_id)
    meter_id, timestamp, demand_kw, solar_kw, net_grid_kw, is_fault
FROM meter_readings
ORDER BY meter_id, timestamp DESC
"""

# Stage 1 sums all meters at each timestamp (system load at that instant).
# Stage 2 averages those system totals inside each time bucket. Aggregating raw
# rows directly would double-count meters that report several times per bucket.
HISTORY_SQL = """
WITH system_totals AS (
    SELECT
        timestamp,
        sum(demand_kw) AS demand_kw,
        sum(solar_kw) AS solar_kw,
        sum(net_grid_kw) AS net_grid_kw,
        count(*) AS reading_count,
        count(*) FILTER (WHERE is_fault) AS fault_count
    FROM meter_readings
    WHERE timestamp >= $1 AND timestamp < $2
    GROUP BY timestamp
)
SELECT
    time_bucket($3::interval, timestamp) AS bucket_start,
    avg(demand_kw) AS avg_demand_kw,
    avg(solar_kw) AS avg_solar_kw,
    avg(net_grid_kw) AS avg_net_grid_kw,
    max(net_grid_kw) AS max_net_grid_kw,
    sum(reading_count)::int AS reading_count,
    sum(fault_count)::int AS fault_count
FROM system_totals
GROUP BY bucket_start
ORDER BY bucket_start
"""


# Energy (kWh) = power (kW) x hours. LEAD() finds each meter's next reading
# time without collapsing rows, so each reading's power is assumed to hold until
# that meter's next sample (left Riemann sum). Gaps longer than $3 seconds are
# treated as missing data and contribute no energy; the last reading in the
# range has no successor and also contributes none.
KPI_SQL = """
WITH ordered AS (
    SELECT
        meter_id,
        timestamp,
        demand_kw,
        solar_kw,
        net_grid_kw,
        is_fault,
        EXTRACT(EPOCH FROM (
            LEAD(timestamp) OVER (PARTITION BY meter_id ORDER BY timestamp) - timestamp
        ))::double precision AS gap_seconds
    FROM meter_readings
    WHERE timestamp >= $1 AND timestamp < $2
),
weighted AS (
    SELECT
        *,
        CASE WHEN gap_seconds <= $3 THEN gap_seconds / 3600.0 ELSE 0 END AS hours
    FROM ordered
),
system_peak AS (
    SELECT timestamp, sum(net_grid_kw) AS net_grid_kw
    FROM ordered
    GROUP BY timestamp
    ORDER BY net_grid_kw DESC, timestamp
    LIMIT 1
)
SELECT
    count(DISTINCT meter_id)::int AS meter_count,
    count(*)::int AS reading_count,
    (count(*) FILTER (WHERE is_fault))::int AS fault_count,
    coalesce(sum(demand_kw * hours), 0) AS demand_kwh,
    coalesce(sum(solar_kw * hours), 0) AS solar_kwh,
    coalesce(sum(GREATEST(net_grid_kw, 0) * hours), 0) AS import_kwh,
    coalesce(sum(GREATEST(-net_grid_kw, 0) * hours), 0) AS export_kwh,
    (SELECT net_grid_kw FROM system_peak) AS peak_net_grid_kw,
    (SELECT timestamp FROM system_peak) AS peak_at
FROM weighted
"""

MAX_ENERGY_GAP_SECONDS = 900


class ReadingRepository(Protocol):
    async def save_batch(
        self, readings: Sequence[ReadingMessage], alerts: Sequence[AnomalyAlert]
    ) -> None: ...

    async def get_latest_by_meter(self) -> Sequence[ReadingMessage]: ...

    async def count_readings(self) -> int: ...

    async def get_history(self, window: HistoryWindow) -> Sequence[HistoryBucket]: ...

    async def get_kpis(self, time_range: TimeRange) -> KpiSummary: ...

    async def get_alerts(
        self, time_range: TimeRange, limit: int
    ) -> Sequence[AnomalyAlert]: ...


class PostgresReadingRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def initialize(self) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute(SCHEMA_SQL)

    async def save_batch(
        self, readings: Sequence[ReadingMessage], alerts: Sequence[AnomalyAlert]
    ) -> None:
        """Save readings and their alerts in one transaction (one commit)."""
        if not readings and not alerts:
            return
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                # executemany runs one statement per row, so a replayed
                # duplicate key within a batch is handled by the upsert rather
                # than failing as a single multi-row INSERT would.
                if readings:
                    await connection.executemany(
                        INSERT_SQL,
                        [
                            (r.timestamp, r.meter_id, r.demand_kw, r.solar_kw, r.net_grid_kw, r.is_fault)
                            for r in readings
                        ],
                    )
                if alerts:
                    await connection.executemany(
                        INSERT_ALERT_SQL,
                        [
                            (a.timestamp, a.meter_id, a.demand_kw, a.baseline_mean_kw, a.baseline_std_kw, a.z_score)
                            for a in alerts
                        ],
                    )

    async def get_latest_by_meter(self) -> Sequence[ReadingMessage]:
        rows = await self._pool.fetch(LATEST_SQL)
        return [ReadingMessage.model_validate(dict(row)) for row in rows]

    async def count_readings(self) -> int:
        return await self._pool.fetchval("SELECT count(*) FROM meter_readings")

    async def get_history(self, window: HistoryWindow) -> Sequence[HistoryBucket]:
        rows = await self._pool.fetch(HISTORY_SQL, window.start, window.end, window.bucket)
        return [HistoryBucket.model_validate(dict(row)) for row in rows]

    async def get_kpis(self, time_range: TimeRange) -> KpiSummary:
        row = await self._pool.fetchrow(
            KPI_SQL, time_range.start, time_range.end, MAX_ENERGY_GAP_SECONDS
        )
        values = dict(row)
        return KpiSummary(
            start=time_range.start,
            end=time_range.end,
            solar_coverage_pct=solar_coverage_pct(values["demand_kwh"], values["import_kwh"]),
            **values,
        )

    async def get_alerts(self, time_range: TimeRange, limit: int) -> Sequence[AnomalyAlert]:
        rows = await self._pool.fetch(
            RECENT_ALERTS_SQL, time_range.start, time_range.end, limit
        )
        return [AnomalyAlert.model_validate(dict(row)) for row in rows]
