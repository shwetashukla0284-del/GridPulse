"""
Measure end-to-end throughput and latency: Redis publish -> FastAPI consumer
(validate, persist, detect) -> batched WebSocket delivery.

Start an API that listens on a dedicated channel, then run this script:

    READINGS_CHANNEL=gridpulse:loadtest uvicorn backend.app.main:app --port 8001
    python -m tests.load_test --api http://127.0.0.1:8001 --rates 100 500 1000

Publisher and WebSocket client run in this one process, so send and receive
times come from the same clock. Test readings use `load-` meter IDs and
timestamps in 2030 to stay out of the dashboard's recent views.
"""

import argparse
import asyncio
import json
import math
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import websockets
from redis.asyncio import Redis

BASE_TIME = datetime(2030, 1, 1, tzinfo=timezone.utc)
DRAIN_TIMEOUT_S = 30.0
SETTLE_TIMEOUT_S = 180.0


@dataclass
class StageResult:
    target_rate: int
    published: int
    publish_seconds: float
    delivered: int
    delivery_seconds: float
    latencies_ms: list[float]
    persisted: int
    dropped_live: int

    @property
    def achieved_publish_rate(self) -> float:
        return self.published / self.publish_seconds

    @property
    def throughput(self) -> float:
        return self.delivered / self.delivery_seconds if self.delivery_seconds else 0.0


def percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile: the smallest value with pct% of values at or below it."""
    if not sorted_values:
        return math.nan
    rank = max(1, math.ceil(pct / 100 * len(sorted_values)))
    return sorted_values[rank - 1]


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def make_message(index: int, meters: int) -> tuple[tuple[str, datetime], str]:
    meter_id = f"load-{index % meters:03d}"
    timestamp = BASE_TIME + timedelta(seconds=index // meters)
    # Small deterministic variation keeps values realistic for the detector.
    demand = 1.0 + 0.05 * ((index * 7919) % 11 - 5) / 5
    payload = json.dumps(
        {
            "meter_id": meter_id,
            "timestamp": timestamp.isoformat(),
            "demand_kw": demand,
            "solar_kw": 0.0,
            "net_grid_kw": demand,
            "is_fault": False,
        }
    )
    return (meter_id, timestamp), payload


async def run_stage(
    redis: Redis,
    channel: str,
    api: str,
    rate: int,
    duration_s: float,
    meters: int,
    start_index: int,
    sent_at: dict[tuple[str, datetime], float],
    received_at: dict[tuple[str, datetime], float],
) -> tuple[StageResult, int]:
    total = int(rate * duration_s)
    keys = []
    health_before = get_json(f"{api}/health")
    count_before = get_json(f"{api}/readings/count")["count"]

    start = time.perf_counter()
    for offset in range(total):
        # Absolute schedule: if a sleep overshoots (Windows timers are coarse),
        # the next messages are sent immediately to catch up, so the average
        # rate holds even though sends come in small bursts.
        target = start + offset / rate
        delay = target - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        key, payload = make_message(start_index + offset, meters)
        sent_at[key] = time.perf_counter()
        await redis.publish(channel, payload)
        keys.append(key)
    publish_seconds = time.perf_counter() - start

    deadline = time.perf_counter() + DRAIN_TIMEOUT_S
    while time.perf_counter() < deadline and not all(k in received_at for k in keys):
        await asyncio.sleep(0.05)

    delivered_keys = [k for k in keys if k in received_at]
    latencies = sorted((received_at[k] - sent_at[k]) * 1000 for k in delivered_keys)
    last_receive = max((received_at[k] for k in delivered_keys), default=start)

    # Wait until the server has consumed this stage's messages, so a backlog
    # cannot spill into the next stage and distort its measurements.
    settle_deadline = time.perf_counter() + SETTLE_TIMEOUT_S
    while (
        get_json(f"{api}/health")["readings_received"] - health_before["readings_received"] < total
        and time.perf_counter() < settle_deadline
    ):
        await asyncio.sleep(0.5)
    health_after = get_json(f"{api}/health")
    count_after = get_json(f"{api}/readings/count")["count"]

    return (
        StageResult(
            target_rate=rate,
            published=total,
            publish_seconds=publish_seconds,
            delivered=len(delivered_keys),
            delivery_seconds=last_receive - start,
            latencies_ms=latencies,
            persisted=count_after - count_before,
            dropped_live=health_after["websocket_readings_dropped"]
            - health_before["websocket_readings_dropped"],
        ),
        start_index + total,
    )


async def receive(ws, received_at: dict[tuple[str, datetime], float]) -> None:
    async for frame in ws:
        now = time.perf_counter()
        for reading in json.loads(frame)["readings"]:
            key = (reading["meter_id"], datetime.fromisoformat(reading["timestamp"]))
            received_at.setdefault(key, now)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="http://127.0.0.1:8001")
    parser.add_argument("--redis-url", default="redis://127.0.0.1:6379/0")
    parser.add_argument("--channel", default="gridpulse:loadtest")
    parser.add_argument("--rates", type=int, nargs="+", default=[25, 50, 100, 200])
    parser.add_argument("--duration", type=float, default=10.0, help="seconds per stage")
    parser.add_argument("--meters", type=int, default=10)
    args = parser.parse_args()

    ws_url = args.api.replace("http", "ws", 1) + "/ws/readings"
    redis = Redis.from_url(args.redis_url)
    sent_at: dict[tuple[str, datetime], float] = {}
    received_at: dict[tuple[str, datetime], float] = {}

    # Continue after any earlier run so timestamps stay unique (no upserts).
    start_index = int(time.time() * 1000) % 10_000_000 * args.meters

    results: list[StageResult] = []
    async with websockets.connect(ws_url, max_size=None) as ws:
        receiver = asyncio.create_task(receive(ws, received_at))
        try:
            for rate in args.rates:
                result, start_index = await run_stage(
                    redis, args.channel, args.api, rate, args.duration,
                    args.meters, start_index, sent_at, received_at,
                )
                results.append(result)
                print(f"stage {rate}/s done: delivered {result.delivered}/{result.published}", flush=True)
                await asyncio.sleep(2)  # let the server settle between stages
        finally:
            receiver.cancel()
            await redis.aclose()

    print()
    print(f"{'target/s':>8} {'sent/s':>7} {'deliv/s':>8} {'delivered':>11} {'persisted':>9} "
          f"{'dropped':>7} {'p50 ms':>7} {'p95 ms':>7} {'p99 ms':>7} {'max ms':>8}")
    for r in results:
        lat = r.latencies_ms
        print(
            f"{r.target_rate:>8} {r.achieved_publish_rate:>7.0f} {r.throughput:>8.0f} "
            f"{r.delivered:>5}/{r.published:<5} {r.persisted:>9} {r.dropped_live:>7} "
            f"{percentile(lat, 50):>7.1f} {percentile(lat, 95):>7.1f} "
            f"{percentile(lat, 99):>7.1f} {(lat[-1] if lat else math.nan):>8.1f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
