import argparse
import random
import time
from datetime import datetime, timedelta, timezone

from redis import Redis

from simulator.publisher import publish_reading, serialize_reading
from simulator.simulation import simulate_readings


def _parse_start_time(value: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "start time must be ISO 8601, for example 2026-06-01T12:00:00+00:00"
        ) from error

    if timestamp.utcoffset() is None:
        raise argparse.ArgumentTypeError("start time must include a timezone offset")
    return timestamp


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate simulated smart-meter readings.")
    parser.add_argument("--meters", type=int, default=3, help="number of meters (default: 3)")
    parser.add_argument("--steps", type=int, default=5, help="time steps to generate (default: 5)")
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=60,
        help="virtual seconds between steps (default: 60)",
    )
    parser.add_argument("--seed", type=int, help="optional random seed for repeatable output")
    parser.add_argument(
        "--redis-url",
        help="optional Redis URL; publish readings to Redis in addition to JSONL output",
    )
    parser.add_argument(
        "--realtime",
        action="store_true",
        help="wait between virtual time steps instead of generating as fast as possible",
    )
    parser.add_argument(
        "--start-time",
        type=_parse_start_time,
        help="ISO 8601 start time with timezone (default: current UTC time)",
    )
    args = parser.parse_args()

    if args.meters < 1:
        parser.error("--meters must be at least 1")
    if args.steps < 1:
        parser.error("--steps must be at least 1")
    if args.interval_seconds < 1:
        parser.error("--interval-seconds must be at least 1")

    start_time = args.start_time or datetime.now(timezone.utc)
    meter_ids = [f"meter-{number:03d}" for number in range(1, args.meters + 1)]
    readings = simulate_readings(
        meter_ids,
        start_time,
        args.steps,
        timedelta(seconds=args.interval_seconds),
        random.Random(args.seed),
    )

    redis_client = Redis.from_url(args.redis_url) if args.redis_url else None
    try:
        if redis_client is not None:
            redis_client.ping()

        total_readings = args.meters * args.steps
        for reading_number, reading in enumerate(readings, start=1):
            message = serialize_reading(reading)
            print(message, flush=args.realtime)
            if redis_client is not None:
                publish_reading(redis_client, reading)

            is_last_meter_in_step = reading_number % args.meters == 0
            is_last_reading = reading_number == total_readings
            if args.realtime and is_last_meter_in_step and not is_last_reading:
                time.sleep(args.interval_seconds)
    finally:
        if redis_client is not None:
            redis_client.close()


if __name__ == "__main__":
    main()
