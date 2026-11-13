import json
import random
import unittest
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest.mock import Mock, call, patch

from simulator.__main__ import main
from simulator.generator import generate_reading
from simulator.profiles import estimate_demand_kw
from simulator.publisher import READINGS_CHANNEL, publish_reading
from simulator.reading import MeterReading
from simulator.simulation import simulate_readings


class GenerateReadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.timestamp = datetime(2026, 6, 1, 12, 0)

    def test_seeded_generator_is_reproducible(self) -> None:
        first = generate_reading("meter-1", self.timestamp, random.Random(42))
        second = generate_reading("meter-1", self.timestamp, random.Random(42))

        self.assertEqual(first, second)

    def test_fault_probability_marks_and_injects_surge(self) -> None:
        reading = generate_reading(
            "meter-1",
            self.timestamp,
            random.Random(42),
            demand_noise_std_kw=0.0,
            solar_noise_std_kw=0.0,
            fault_probability=1.0,
        )

        self.assertTrue(reading.is_fault)
        self.assertGreaterEqual(
            reading.demand_kw,
            estimate_demand_kw(self.timestamp) + 3.0,
        )
        self.assertLessEqual(
            reading.demand_kw,
            estimate_demand_kw(self.timestamp) + 8.0,
        )

    def test_zero_fault_probability_does_not_inject_surge(self) -> None:
        reading = generate_reading(
            "meter-1",
            self.timestamp,
            random.Random(42),
            demand_noise_std_kw=0.0,
            solar_noise_std_kw=0.0,
            fault_probability=0.0,
        )

        self.assertFalse(reading.is_fault)
        self.assertEqual(reading.demand_kw, estimate_demand_kw(self.timestamp))

    def test_measurements_are_clamped_at_zero(self) -> None:
        reading = generate_reading(
            "meter-1",
            self.timestamp,
            random.Random(42),
            demand_noise_std_kw=100.0,
            solar_noise_std_kw=100.0,
            fault_probability=0.0,
        )

        self.assertGreaterEqual(reading.demand_kw, 0.0)
        self.assertGreaterEqual(reading.solar_kw, 0.0)

    def test_invalid_noise_and_fault_probability_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            generate_reading(
                "meter-1",
                self.timestamp,
                random.Random(42),
                demand_noise_std_kw=-0.1,
            )
        with self.assertRaises(ValueError):
            generate_reading(
                "meter-1",
                self.timestamp,
                random.Random(42),
                fault_probability=1.1,
            )


class SimulateReadingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start_time = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

    def test_generates_one_reading_per_meter_per_step(self) -> None:
        readings = list(
            simulate_readings(
                ["meter-1", "meter-2"],
                self.start_time,
                3,
                timedelta(minutes=1),
                random.Random(42),
            )
        )

        self.assertEqual(len(readings), 6)
        self.assertEqual(
            [(reading.meter_id, reading.timestamp) for reading in readings],
            [
                (meter_id, self.start_time + step * timedelta(minutes=1))
                for step in range(3)
                for meter_id in ("meter-1", "meter-2")
            ],
        )

    def test_same_seed_reproduces_the_readings(self) -> None:
        args = (["meter-1"], self.start_time, 2, timedelta(minutes=1))

        first = list(simulate_readings(*args, random.Random(7)))
        second = list(simulate_readings(*args, random.Random(7)))

        self.assertEqual(first, second)

    def test_rejects_invalid_simulation_configuration(self) -> None:
        invalid_cases = [
            ([], self.start_time, 1, timedelta(minutes=1)),
            (["meter-1", "meter-1"], self.start_time, 1, timedelta(minutes=1)),
            (["meter-1"], datetime(2026, 6, 1), 1, timedelta(minutes=1)),
            (["meter-1"], self.start_time, 0, timedelta(minutes=1)),
            (["meter-1"], self.start_time, 1, timedelta(0)),
        ]

        for meter_ids, start_time, steps, interval in invalid_cases:
            with self.subTest(meter_ids=meter_ids, steps=steps, interval=interval):
                with self.assertRaises(ValueError):
                    list(
                        simulate_readings(
                            meter_ids,
                            start_time,
                            steps,
                            interval,
                            random.Random(42),
                        )
                    )


class SimulatorCliTests(unittest.TestCase):
    def run_cli(self, *arguments: str) -> str:
        output = StringIO()
        with (
            patch(
                "sys.argv",
                [
                    "simulator",
                    "--meters",
                    "2",
                    "--steps",
                    "3",
                    "--interval-seconds",
                    "7",
                    "--seed",
                    "42",
                    "--start-time",
                    "2026-06-01T12:00:00+00:00",
                    *arguments,
                ],
            ),
            patch("sys.stdout", output),
        ):
            main()
        return output.getvalue()

    @patch("simulator.__main__.time.sleep")
    def test_realtime_waits_between_steps_not_between_meters(self, sleep) -> None:
        output = self.run_cli("--realtime")

        self.assertEqual(len(output.splitlines()), 6)
        self.assertEqual(sleep.call_args_list, [call(7), call(7)])

    @patch("simulator.__main__.time.sleep")
    def test_default_cli_does_not_wait(self, sleep) -> None:
        output = self.run_cli()

        self.assertEqual(len(output.splitlines()), 6)
        sleep.assert_not_called()

    @patch("simulator.__main__.Redis.from_url")
    def test_cli_publishes_to_redis_when_url_is_given(self, from_url) -> None:
        output = self.run_cli("--redis-url", "redis://localhost:6379/0")

        client = from_url.return_value
        client.ping.assert_called_once_with()
        self.assertEqual(client.publish.call_count, 6)
        client.close.assert_called_once_with()
        self.assertEqual(len(output.splitlines()), 6)


class ReadingPublisherTests(unittest.TestCase):
    def test_publish_serializes_reading_and_returns_subscriber_count(self) -> None:
        reading = MeterReading(
            "meter-1",
            datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
            2.5,
            1.0,
            False,
        )
        redis_client = Mock()
        redis_client.publish.return_value = 1

        subscribers = publish_reading(redis_client, reading)

        self.assertEqual(subscribers, 1)
        channel, message = redis_client.publish.call_args.args
        self.assertEqual(channel, READINGS_CHANNEL)
        self.assertEqual(
            json.loads(message),
            {
                "meter_id": "meter-1",
                "timestamp": "2026-06-01T12:00:00+00:00",
                "demand_kw": 2.5,
                "solar_kw": 1.0,
                "net_grid_kw": 1.5,
                "is_fault": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
