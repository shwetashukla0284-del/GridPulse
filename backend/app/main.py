import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import AsyncIterator

import asyncpg
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from starlette.websockets import WebSocketDisconnect

from backend.app.anomaly import RollingZScoreDetector
from backend.app.consumer import LiveEvent, ReadingState, consume_readings
from backend.app.history import (
    MAX_KPI_RANGE,
    resolve_history_window,
    resolve_time_range,
)
from backend.app.models import AnomalyAlert, HistoryBucket, KpiSummary, ReadingMessage
from backend.app.repository import PostgresReadingRepository
from backend.app.websocket import WebSocketHub, broadcast_batches
from simulator.publisher import READINGS_CHANNEL

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    # Overridable so a load test can use its own channel, isolated from other
    # API instances and dev traffic.
    channel = os.getenv("READINGS_CHANNEL", READINGS_CHANNEL)
    # 1 reproduces one commit per reading (useful for A/B load tests).
    max_batch_size = int(os.getenv("CONSUMER_MAX_BATCH", "500"))
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql://gridpulse:gridpulse_dev@127.0.0.1:5432/gridpulse",
    )
    redis_client = Redis.from_url(redis_url, decode_responses=True)
    database_pool = await asyncpg.create_pool(database_url)
    repository = PostgresReadingRepository(database_pool)

    try:
        await repository.initialize()
        await redis_client.ping()
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(channel)
    except Exception:
        await database_pool.close()
        await redis_client.aclose()
        raise

    state = ReadingState()
    websocket_queue: asyncio.Queue[LiveEvent] = asyncio.Queue(maxsize=1000)
    # In-memory per-meter baselines: lost on restart, so each meter needs a
    # fresh warm-up before it can be flagged again.
    detector = RollingZScoreDetector()
    websocket_hub = WebSocketHub()
    batch_task = asyncio.create_task(
        broadcast_batches(websocket_queue, websocket_hub)
    )
    consumer_task = asyncio.create_task(
        consume_readings(
            pubsub, state, repository, websocket_queue, detector, max_batch_size
        )
    )
    consumer_task.add_done_callback(_log_consumer_failure)
    batch_task.add_done_callback(_log_consumer_failure)
    app.state.redis_client = redis_client
    app.state.pubsub = pubsub
    app.state.database_pool = database_pool
    app.state.reading_repository = repository
    app.state.reading_state = state
    app.state.consumer_task = consumer_task
    app.state.websocket_queue = websocket_queue
    app.state.websocket_hub = websocket_hub
    app.state.batch_task = batch_task

    logger.info("Subscribed to Redis channel %s", channel)
    try:
        yield
    finally:
        for task in (consumer_task, batch_task):
            task.cancel()
        for task in (consumer_task, batch_task):
            with suppress(asyncio.CancelledError):
                await task
        await pubsub.aclose()
        await redis_client.aclose()
        await database_pool.close()


app = FastAPI(title="GridPulse API", lifespan=lifespan)

# The dashboard runs on a different origin (port 3000), so browsers need CORS
# permission to read REST responses. WebSockets are not subject to this check.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
        ).split(",")
        if origin.strip()
    ],
    allow_methods=["GET"],
)


def _log_consumer_failure(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error(
            "Redis reading consumer failed",
            exc_info=(type(error), error, error.__traceback__),
        )


@app.get("/health")
async def health(request: Request) -> dict[str, str | int]:
    consumer_task: asyncio.Task[None] = request.app.state.consumer_task
    batch_task: asyncio.Task[None] = request.app.state.batch_task
    if consumer_task.done() or batch_task.done():
        logger.error("Redis consumer or WebSocket batcher stopped unexpectedly")
        raise HTTPException(status_code=503, detail="A background service is not running")

    state: ReadingState = request.app.state.reading_state
    return {
        "status": "ok",
        "consumer": "running",
        "readings_received": state.readings_received,
        "anomalies_detected": state.anomalies_detected,
        "batches_saved": state.batches_saved,
        "largest_batch": state.largest_batch,
        "websocket_clients": request.app.state.websocket_hub.client_count,
        "websocket_readings_dropped": state.websocket_readings_dropped,
    }


@app.get("/readings/latest", response_model=list[ReadingMessage])
async def latest_readings(request: Request) -> list[ReadingMessage]:
    repository: PostgresReadingRepository = request.app.state.reading_repository
    return list(await repository.get_latest_by_meter())


@app.get("/readings/count")
async def reading_count(request: Request) -> dict[str, int]:
    repository: PostgresReadingRepository = request.app.state.reading_repository
    return {"count": await repository.count_readings()}


@app.get("/readings/history", response_model=list[HistoryBucket])
async def reading_history(
    request: Request,
    start: datetime | None = None,
    end: datetime | None = None,
    bucket_seconds: int = 60,
) -> list[HistoryBucket]:
    try:
        window = resolve_history_window(
            start, end, bucket_seconds, now=datetime.now(timezone.utc)
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    repository: PostgresReadingRepository = request.app.state.reading_repository
    return list(await repository.get_history(window))


@app.get("/analytics/kpis", response_model=KpiSummary)
async def kpi_summary(
    request: Request,
    start: datetime | None = None,
    end: datetime | None = None,
) -> KpiSummary:
    try:
        time_range = resolve_time_range(
            start, end, now=datetime.now(timezone.utc), max_range=MAX_KPI_RANGE
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    repository: PostgresReadingRepository = request.app.state.reading_repository
    return await repository.get_kpis(time_range)


@app.get("/alerts", response_model=list[AnomalyAlert])
async def recent_alerts(
    request: Request,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 100,
) -> list[AnomalyAlert]:
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")
    try:
        time_range = resolve_time_range(
            start, end, now=datetime.now(timezone.utc), max_range=MAX_KPI_RANGE
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    repository: PostgresReadingRepository = request.app.state.reading_repository
    return list(await repository.get_alerts(time_range, limit))


@app.websocket("/ws/readings")
async def stream_readings(websocket: WebSocket) -> None:
    hub: WebSocketHub = websocket.app.state.websocket_hub
    await hub.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(websocket)
