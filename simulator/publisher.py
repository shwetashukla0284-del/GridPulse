import json

from redis import Redis

from simulator.reading import MeterReading

READINGS_CHANNEL = "gridpulse:readings"


def serialize_reading(reading: MeterReading) -> str:
    return json.dumps(
        {
            "meter_id": reading.meter_id,
            "timestamp": reading.timestamp.isoformat(),
            "demand_kw": reading.demand_kw,
            "solar_kw": reading.solar_kw,
            "net_grid_kw": reading.net_grid_kw,
            "is_fault": reading.is_fault,
        },
        separators=(",", ":"),
        allow_nan=False,
    )


def publish_reading(
    redis_client: Redis,
    reading: MeterReading,
    channel: str = READINGS_CHANNEL,
) -> int:
    """Publish one serialized reading and return the subscriber count."""
    return redis_client.publish(channel, serialize_reading(reading))
