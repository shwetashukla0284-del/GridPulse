import asyncio
import logging
from collections.abc import Sequence

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from backend.app.models import AnomalyAlert, ReadingMessage

logger = logging.getLogger(__name__)


class WebSocketHub:
    def __init__(self, send_timeout_seconds: float = 1.0) -> None:
        self._clients: set[WebSocket] = set()
        self._send_timeout_seconds = send_timeout_seconds

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        logger.info("WebSocket client connected; clients=%d", self.client_count)

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)
        logger.info("WebSocket client disconnected; clients=%d", self.client_count)

    async def broadcast(
        self,
        readings: Sequence[ReadingMessage],
        alerts: Sequence[AnomalyAlert] = (),
    ) -> None:
        if not (readings or alerts) or not self._clients:
            return

        clients = tuple(self._clients)
        # `alerts` is an added field on the existing message type, so clients
        # that only understand readings keep working.
        payload = {
            "type": "readings",
            "readings": [reading.model_dump(mode="json") for reading in readings],
            "alerts": [alert.model_dump(mode="json") for alert in alerts],
        }
        results = await asyncio.gather(
            *(
                asyncio.wait_for(
                    client.send_json(payload),
                    timeout=self._send_timeout_seconds,
                )
                for client in clients
            ),
            return_exceptions=True,
        )

        for client, result in zip(clients, results):
            if isinstance(result, (WebSocketDisconnect, TimeoutError)):
                logger.warning("Removing WebSocket client after send failure: %s", result)
                self.disconnect(client)
            elif isinstance(result, Exception):
                logger.error(
                    "Removing WebSocket client after unexpected send failure",
                    exc_info=(type(result), result, result.__traceback__),
                )
                self.disconnect(client)


async def broadcast_batches(
    queue: asyncio.Queue[ReadingMessage | AnomalyAlert],
    hub: WebSocketHub,
    flush_interval_seconds: float = 0.1,
    max_batch_size: int = 100,
) -> None:
    if flush_interval_seconds <= 0:
        raise ValueError("flush_interval_seconds must be positive")
    if max_batch_size < 1:
        raise ValueError("max_batch_size must be at least 1")

    loop = asyncio.get_running_loop()
    while True:
        first_reading = await queue.get()
        batch = [first_reading]
        deadline = loop.time() + flush_interval_seconds

        while len(batch) < max_batch_size:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                batch.append(await asyncio.wait_for(queue.get(), timeout=remaining))
            except TimeoutError:
                break

        readings = [event for event in batch if isinstance(event, ReadingMessage)]
        alerts = [event for event in batch if isinstance(event, AnomalyAlert)]
        await hub.broadcast(readings, alerts)
