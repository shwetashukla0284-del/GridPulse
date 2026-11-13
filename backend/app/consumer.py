import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol

from pydantic import ValidationError

from backend.app.anomaly import RollingZScoreDetector
from backend.app.models import AnomalyAlert, ReadingMessage
from backend.app.repository import ReadingRepository

logger = logging.getLogger(__name__)

# Readings and alerts share one ordered live queue, so an alert is broadcast
# in the same batch as (or after) the reading that triggered it.
LiveEvent = ReadingMessage | AnomalyAlert


class MessageStream(Protocol):
    def listen(self) -> AsyncIterator[Mapping[str, Any]]: ...


class ReadingState:
    def __init__(self) -> None:
        self.readings_received = 0
        self.anomalies_detected = 0
        self.websocket_readings_dropped = 0
        self.batches_saved = 0
        self.largest_batch = 0


def _enqueue_live(
    queue: asyncio.Queue[LiveEvent], event: LiveEvent, state: ReadingState
) -> None:
    if queue.full():
        queue.get_nowait()
        state.websocket_readings_dropped += 1
        logger.warning(
            "WebSocket queue full; dropping oldest live update (dropped=%d)",
            state.websocket_readings_dropped,
        )
    queue.put_nowait(event)


_END_OF_STREAM = object()


async def _read_into(
    pubsub: MessageStream, inbox: asyncio.Queue[object]
) -> None:
    """Move published payloads into a bounded inbox. When the inbox is full,
    `put` waits, so a backlog stays in Redis instead of growing memory here."""
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await inbox.put(message["data"])
    finally:
        await inbox.put(_END_OF_STREAM)


async def consume_readings(
    pubsub: MessageStream,
    state: ReadingState,
    repository: ReadingRepository,
    websocket_queue: asyncio.Queue[LiveEvent],
    detector: RollingZScoreDetector,
    max_batch_size: int = 500,
) -> None:
    """
    Natural batching: wait for one message, then also take any messages that
    are already waiting (up to `max_batch_size`) and save them in one
    transaction. At low load batches are tiny, so no latency is added; under
    load they grow and many readings share one commit.
    """
    if max_batch_size < 1:
        raise ValueError("max_batch_size must be at least 1")

    inbox: asyncio.Queue[object] = asyncio.Queue(maxsize=max_batch_size * 4)
    reader = asyncio.create_task(_read_into(pubsub, inbox))
    try:
        finished = False
        while not finished:
            payloads = [await inbox.get()]
            while len(payloads) < max_batch_size and not inbox.empty():
                payloads.append(inbox.get_nowait())
            if _END_OF_STREAM in payloads:
                payloads = payloads[: payloads.index(_END_OF_STREAM)]
                finished = True
            await _process_batch(payloads, state, repository, websocket_queue, detector)
        # Surface a failure from the Redis reader (e.g. a lost connection).
        await reader
    finally:
        reader.cancel()


async def _process_batch(
    payloads: list[object],
    state: ReadingState,
    repository: ReadingRepository,
    websocket_queue: asyncio.Queue[LiveEvent],
    detector: RollingZScoreDetector,
) -> None:
    readings: list[ReadingMessage] = []
    events: list[LiveEvent] = []
    alerts: list[AnomalyAlert] = []
    for data in payloads:
        try:
            reading = ReadingMessage.model_validate_json(data)  # type: ignore[arg-type]
        except ValidationError as error:
            logger.warning("Ignoring invalid reading: %s", error)
            continue
        readings.append(reading)
        events.append(reading)
        alert = detector.observe(reading)
        if alert is not None:
            alerts.append(alert)
            events.append(alert)
            logger.info(
                "Anomaly on %s at %s: %.2f kW (z=%.1f)",
                alert.meter_id,
                alert.timestamp.isoformat(),
                alert.demand_kw,
                alert.z_score,
            )

    if not readings:
        return
    # Persist before live delivery so the database stays complete even if the
    # bounded live queue later drops an update.
    await repository.save_batch(readings, alerts)
    for event in events:
        _enqueue_live(websocket_queue, event, state)
    state.readings_received += len(readings)
    state.anomalies_detected += len(alerts)
    state.batches_saved += 1
    state.largest_batch = max(state.largest_batch, len(readings))
