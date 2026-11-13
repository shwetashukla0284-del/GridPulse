import unittest
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from pydantic import ValidationError

from backend.app.anomaly import RollingZScoreDetector
from backend.app.consumer import LiveEvent, ReadingState, consume_readings
from backend.app.history import (
    MAX_KPI_RANGE,
    HistoryWindow,
    TimeRange,
    resolve_history_window,
    resolve_time_range,
    solar_coverage_pct,
)
from backend.app.models import AnomalyAlert, HistoryBucket, KpiSummary, ReadingMessage
from backend.app.repository import HISTORY_SQL, INSERT_ALERT_SQL, KPI_SQL, SCHEMA_SQL
from backend.app.websocket import WebSocketHub, broadcast_batches
from simulator.publisher import READINGS_CHANNEL, serialize_reading
from simulator.reading import MeterReading


class ReadingMessageTests(unittest.TestCase):
    def test_accepts_a_valid_reading(self) -> None:
        reading = ReadingMessage(
            meter_id="meter-1",
            timestamp="2026-06-01T12:00:00+00:00",
            demand_kw=2.5,
            solar_kw=1.0,
            net_grid_kw=1.5,
            is_fault=False,
        )

        self.assertEqual(reading.net_grid_kw, 1.5)

    def test_rejects_naive_timestamp_and_inconsistent_net_power(self) -> None:
        values = {
            "meter_id": "meter-1",
            "timestamp": "2026-06-01T12:00:00+00:00",
            "demand_kw": 2.5,
            "solar_kw": 1.0,
            "net_grid_kw": 1.5,
            "is_fault": False,
        }

        with self.assertRaises(ValidationError):
            ReadingMessage(**{**values, "timestamp": "2026-06-01T12:00:00"})
        with self.assertRaises(ValidationError):
            ReadingMessage(**{**values, "net_grid_kw": 99.0})
        with self.assertRaises(ValidationError):
            ReadingMessage(**{**values, "demand_kw": -1.0, "net_grid_kw": -2.0})
        with self.assertRaises(ValidationError):
            ReadingMessage(**{**values, "solar_kw": float("inf")})


class FakePubSub:
    def __init__(self, messages: list[dict[str, str]]) -> None:
        self.messages = messages

    async def listen(self):
        for message in self.messages:
            yield message


class FakeReadingRepository:
    def __init__(self) -> None:
        self.saved_readings: list[ReadingMessage] = []
        self.saved_alerts: list[AnomalyAlert] = []
        self.batch_sizes: list[int] = []

    async def save_batch(
        self, readings: list[ReadingMessage], alerts: list[AnomalyAlert]
    ) -> None:
        self.saved_readings.extend(readings)
        self.saved_alerts.extend(alerts)
        self.batch_sizes.append(len(readings))

    async def get_latest_by_meter(self) -> list[ReadingMessage]:
        latest: dict[str, ReadingMessage] = {}
        for reading in self.saved_readings:
            latest[reading.meter_id] = reading
        return list(latest.values())

    async def count_readings(self) -> int:
        return len(self.saved_readings)

    async def get_history(self, window: HistoryWindow) -> list[HistoryBucket]:
        return []

    async def get_kpis(self, time_range: TimeRange) -> KpiSummary:
        raise NotImplementedError

    async def get_alerts(self, time_range: TimeRange, limit: int) -> list[AnomalyAlert]:
        return self.saved_alerts[:limit]


class KpiTests(unittest.TestCase):
    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

    def test_solar_coverage_is_share_of_demand_not_imported(self) -> None:
        self.assertAlmostEqual(solar_coverage_pct(10.0, 4.0), 60.0)
        self.assertAlmostEqual(solar_coverage_pct(10.0, 10.0), 0.0)
        self.assertIsNone(solar_coverage_pct(0.0, 0.0))

    def test_kpi_range_is_capped(self) -> None:
        with self.assertRaisesRegex(ValueError, "31 days"):
            resolve_time_range(
                self.now - timedelta(days=32), self.now, self.now, MAX_KPI_RANGE
            )
        allowed = resolve_time_range(None, None, self.now, MAX_KPI_RANGE)
        self.assertEqual(allowed.start, self.now - timedelta(hours=1))

    def test_kpi_sql_uses_per_meter_lead_and_gap_limit(self) -> None:
        self.assertIn("LEAD(timestamp) OVER (PARTITION BY meter_id ORDER BY timestamp)", KPI_SQL)
        self.assertIn("CASE WHEN gap_seconds <= $3", KPI_SQL)


class HistoryWindowTests(unittest.TestCase):
    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

    def test_defaults_to_the_last_hour(self) -> None:
        window = resolve_history_window(None, None, 60, self.now)

        self.assertEqual(window.end, self.now)
        self.assertEqual(window.start, self.now - timedelta(hours=1))
        self.assertEqual(window.bucket, timedelta(seconds=60))

    def test_rejects_invalid_windows(self) -> None:
        earlier = self.now - timedelta(minutes=5)
        with self.assertRaisesRegex(ValueError, "end must be after start"):
            resolve_history_window(self.now, earlier, 60, self.now)
        with self.assertRaisesRegex(ValueError, "bucket_seconds"):
            resolve_history_window(None, None, 0, self.now)
        with self.assertRaisesRegex(ValueError, "timezone"):
            resolve_history_window(datetime(2026, 6, 1, 11, 0), None, 60, self.now)

    def test_caps_the_number_of_buckets(self) -> None:
        week_ago = self.now - timedelta(days=7)
        with self.assertRaisesRegex(ValueError, "more than"):
            resolve_history_window(week_ago, self.now, 60, self.now)
        # Same week with hourly buckets is 168 buckets, which is allowed.
        self.assertEqual(
            resolve_history_window(week_ago, self.now, 3600, self.now).bucket,
            timedelta(hours=1),
        )

    def test_cors_allows_only_dashboard_origins_and_get(self) -> None:
        from fastapi.middleware.cors import CORSMiddleware

        from backend.app.main import app

        cors = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
        self.assertEqual(
            cors.kwargs["allow_origins"],
            ["http://localhost:3000", "http://127.0.0.1:3000"],
        )
        self.assertEqual(cors.kwargs["allow_methods"], ["GET"])

    def test_history_sql_aggregates_meters_before_bucketing(self) -> None:
        self.assertIn("WITH system_totals AS", HISTORY_SQL)
        self.assertIn("time_bucket($3::interval, timestamp)", HISTORY_SQL)


class ReadingConsumerTests(unittest.IsolatedAsyncioTestCase):
    async def test_consumes_valid_messages_and_ignores_invalid_payloads(self) -> None:
        reading = MeterReading(
            "meter-1",
            datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
            2.5,
            1.0,
        )
        pubsub = FakePubSub(
            [
                {"type": "subscribe", "data": READINGS_CHANNEL},
                {"type": "message", "data": serialize_reading(reading)},
                {"type": "message", "data": "{invalid-json"},
            ]
        )
        state = ReadingState()
        repository = FakeReadingRepository()
        websocket_queue: asyncio.Queue[LiveEvent] = asyncio.Queue()

        await consume_readings(
            pubsub, state, repository, websocket_queue, RollingZScoreDetector()
        )

        self.assertEqual(state.readings_received, 1)
        self.assertEqual(await repository.count_readings(), 1)
        self.assertEqual(len(await repository.get_latest_by_meter()), 1)
        self.assertEqual(websocket_queue.get_nowait().net_grid_kw, 1.5)

    async def test_database_failure_stops_consumer_with_original_error(self) -> None:
        reading = MeterReading(
            "meter-1",
            datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
            2.5,
            1.0,
        )

        class FailingRepository(FakeReadingRepository):
            async def save_batch(
                self, readings: list[ReadingMessage], alerts: list[AnomalyAlert]
            ) -> None:
                raise RuntimeError("database unavailable")

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            await consume_readings(
                FakePubSub(
                    [{"type": "message", "data": serialize_reading(reading)}]
                ),
                ReadingState(),
                FailingRepository(),
                asyncio.Queue(),
                RollingZScoreDetector(),
            )

    async def test_detected_anomaly_is_saved_and_queued_after_its_reading(self) -> None:
        start = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        readings = [
            MeterReading("meter-1", start + timedelta(minutes=i), 1.1 if i % 2 else 0.9, 0.0)
            for i in range(12)
        ] + [MeterReading("meter-1", start + timedelta(minutes=12), 6.0, 0.0, True)]
        pubsub = FakePubSub(
            [{"type": "message", "data": serialize_reading(r)} for r in readings]
        )
        state = ReadingState()
        repository = FakeReadingRepository()
        queue: asyncio.Queue[LiveEvent] = asyncio.Queue()

        await consume_readings(pubsub, state, repository, queue, RollingZScoreDetector())

        self.assertEqual(state.readings_received, 13)
        self.assertEqual(state.anomalies_detected, 1)
        self.assertEqual(len(repository.saved_alerts), 1)
        self.assertEqual(repository.saved_alerts[0].demand_kw, 6.0)
        events = [queue.get_nowait() for _ in range(queue.qsize())]
        self.assertIsInstance(events[-2], ReadingMessage)
        self.assertIsInstance(events[-1], AnomalyAlert)

    def _messages(self, count: int) -> list[dict[str, str]]:
        start = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        return [
            {
                "type": "message",
                "data": serialize_reading(
                    MeterReading(f"meter-{i % 3}", start + timedelta(minutes=i), 1.0, 0.0)
                ),
            }
            for i in range(count)
        ]

    async def test_waiting_messages_are_saved_together_up_to_the_limit(self) -> None:
        repository = FakeReadingRepository()
        state = ReadingState()
        queue: asyncio.Queue[LiveEvent] = asyncio.Queue()

        # The fake stream delivers all messages at once, as a backlog would.
        await consume_readings(
            FakePubSub(self._messages(25)), state, repository, queue,
            RollingZScoreDetector(), max_batch_size=10,
        )

        self.assertEqual(sum(repository.batch_sizes), 25)
        self.assertTrue(all(size <= 10 for size in repository.batch_sizes))
        self.assertLess(len(repository.batch_sizes), 25)
        self.assertEqual(state.readings_received, 25)
        self.assertEqual(state.largest_batch, max(repository.batch_sizes))
        # Live delivery keeps publish order across batches.
        delivered = [queue.get_nowait() for _ in range(queue.qsize())]
        self.assertEqual(
            [event.timestamp for event in delivered],
            sorted(event.timestamp for event in delivered),
        )

    async def test_batch_size_one_saves_each_reading_separately(self) -> None:
        repository = FakeReadingRepository()
        await consume_readings(
            FakePubSub(self._messages(5)), ReadingState(), repository,
            asyncio.Queue(), RollingZScoreDetector(), max_batch_size=1,
        )
        self.assertEqual(repository.batch_sizes, [1, 1, 1, 1, 1])

    async def test_redis_reader_failure_stops_consumer(self) -> None:
        class BrokenPubSub:
            async def listen(self):
                yield {"type": "message", "data": "{invalid-json"}
                raise ConnectionError("redis connection lost")

        with self.assertRaisesRegex(ConnectionError, "redis connection lost"):
            await consume_readings(
                BrokenPubSub(), ReadingState(), FakeReadingRepository(),
                asyncio.Queue(), RollingZScoreDetector(),
            )

    async def test_rejects_invalid_batch_size(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_batch_size"):
            await consume_readings(
                FakePubSub([]), ReadingState(), FakeReadingRepository(),
                asyncio.Queue(), RollingZScoreDetector(), max_batch_size=0,
            )

    def test_schema_creates_timescale_hypertable_and_idempotent_key(self) -> None:
        self.assertIn("CREATE EXTENSION IF NOT EXISTS timescaledb", SCHEMA_SQL)
        self.assertIn("create_hypertable", SCHEMA_SQL)
        self.assertIn("PRIMARY KEY (timestamp, meter_id)", SCHEMA_SQL)
        self.assertIn("CREATE TABLE IF NOT EXISTS anomaly_alerts", SCHEMA_SQL)
        self.assertIn("ON CONFLICT (timestamp, meter_id) DO NOTHING", INSERT_ALERT_SQL)


class FakeWebSocket:
    def __init__(self) -> None:
        self.accept = AsyncMock()
        self.send_json = AsyncMock()


class WebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_hub_broadcasts_batch_and_removes_failed_client(self) -> None:
        reading = ReadingMessage(
            meter_id="meter-1",
            timestamp="2026-06-01T12:00:00+00:00",
            demand_kw=2.5,
            solar_kw=1.0,
            net_grid_kw=1.5,
            is_fault=False,
        )
        working_client = FakeWebSocket()
        failed_client = FakeWebSocket()
        failed_client.send_json.side_effect = RuntimeError("socket closed")
        hub = WebSocketHub()
        await hub.connect(working_client)  # type: ignore[arg-type]
        await hub.connect(failed_client)  # type: ignore[arg-type]

        await hub.broadcast([reading])

        working_client.send_json.assert_awaited_once_with(
            {
                "type": "readings",
                "readings": [reading.model_dump(mode="json")],
                "alerts": [],
            }
        )
        self.assertEqual(hub.client_count, 1)

    async def test_batcher_sends_alerts_alongside_readings(self) -> None:
        reading = ReadingMessage(
            meter_id="meter-1",
            timestamp="2026-06-01T12:00:00+00:00",
            demand_kw=6.0,
            solar_kw=0.0,
            net_grid_kw=6.0,
            is_fault=True,
        )
        alert = AnomalyAlert(
            meter_id="meter-1",
            timestamp=reading.timestamp,
            demand_kw=6.0,
            baseline_mean_kw=1.0,
            baseline_std_kw=0.1,
            z_score=50.0,
        )
        queue: asyncio.Queue[LiveEvent] = asyncio.Queue()
        queue.put_nowait(reading)
        queue.put_nowait(alert)
        broadcasted = asyncio.Event()
        hub = AsyncMock(spec=WebSocketHub)

        async def record(_readings: list[ReadingMessage], _alerts: list[AnomalyAlert]) -> None:
            broadcasted.set()

        hub.broadcast.side_effect = record
        task = asyncio.create_task(broadcast_batches(queue, hub, flush_interval_seconds=0.01))
        try:
            await asyncio.wait_for(broadcasted.wait(), timeout=0.2)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual(hub.broadcast.await_args.args, ([reading], [alert]))

    async def test_batcher_flushes_readings_together(self) -> None:
        first = ReadingMessage(
            meter_id="meter-1",
            timestamp="2026-06-01T12:00:00+00:00",
            demand_kw=2.5,
            solar_kw=1.0,
            net_grid_kw=1.5,
            is_fault=False,
        )
        second = first.model_copy(update={"meter_id": "meter-2"})
        queue: asyncio.Queue[ReadingMessage] = asyncio.Queue()
        queue.put_nowait(first)
        queue.put_nowait(second)
        broadcasted = asyncio.Event()
        hub = AsyncMock(spec=WebSocketHub)

        async def record_batch(_readings: list[ReadingMessage], _alerts: object = ()) -> None:
            broadcasted.set()

        hub.broadcast.side_effect = record_batch
        task = asyncio.create_task(
            broadcast_batches(queue, hub, flush_interval_seconds=0.01)
        )
        try:
            await asyncio.wait_for(broadcasted.wait(), timeout=0.2)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual(hub.broadcast.await_count, 1)
        self.assertEqual(hub.broadcast.await_args.args[0], [first, second])

    async def test_batcher_rejects_invalid_settings(self) -> None:
        with self.assertRaisesRegex(ValueError, "flush_interval_seconds"):
            await broadcast_batches(asyncio.Queue(), WebSocketHub(), 0)

    async def test_batcher_respects_maximum_batch_size(self) -> None:
        template = ReadingMessage(
            meter_id="meter-1",
            timestamp="2026-06-01T12:00:00+00:00",
            demand_kw=2.5,
            solar_kw=1.0,
            net_grid_kw=1.5,
            is_fault=False,
        )
        readings = [
            template.model_copy(update={"meter_id": f"meter-{index}"})
            for index in range(3)
        ]
        queue: asyncio.Queue[ReadingMessage] = asyncio.Queue()
        for reading in readings:
            queue.put_nowait(reading)

        batches: list[list[ReadingMessage]] = []
        completed = asyncio.Event()
        hub = AsyncMock(spec=WebSocketHub)

        async def record_batch(batch: list[ReadingMessage], _alerts: object = ()) -> None:
            batches.append(batch)
            if len(batches) == 2:
                completed.set()

        hub.broadcast.side_effect = record_batch
        task = asyncio.create_task(
            broadcast_batches(
                queue,
                hub,
                flush_interval_seconds=0.01,
                max_batch_size=2,
            )
        )
        try:
            await asyncio.wait_for(completed.wait(), timeout=0.2)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual([len(batch) for batch in batches], [2, 1])
