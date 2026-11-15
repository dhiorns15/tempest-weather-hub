import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from weatherhub.cache import LatestCache
from weatherhub.db import init_db, query_history
from weatherhub.poller import UDP_FRESH_SECONDS, keep_fresh_udp_fields, poll_forever
from weatherhub.udp_listener import UDP_SENSOR_FIELDS, parse_obs_st

CLOUD_TIME = 1_794_712_717

# Cloud values are rounded coarser than the local broadcast's.
CLOUD_CONDITIONS = {
    "time": CLOUD_TIME,
    "air_temperature": 45.0,
    "relative_humidity": 90,
    "wind_avg": 3.0,
    "wind_gust": 5.0,
    "wind_direction": 180,
    "station_pressure": 24.9,
    "uv": 0,
    "solar_radiation": 0,
    "feels_like": 43.0,
    "conditions": "Cloudy",
    "icon": "cloudy",
}

UDP_READING = {
    "time": CLOUD_TIME - 59,
    "udp_updated_at": CLOUD_TIME - 59,
    "air_temperature": 45.3,
    "relative_humidity": 90.32,
    "wind_lull": 2.3,
    "wind_avg": 3.3,
    "wind_gust": 4.9,
    "wind_direction": 175,
    "station_pressure": 24.95,
    "uv": 0.01,
    "solar_radiation": 1,
}


def _obs_st_message() -> dict:
    return {
        "type": "obs_st",
        "obs": [
            [1_794_712_658, 1.0, 1.5, 2.2, 175, 3, 845.1, 7.4, 90.3, 0, 0.01, 1, 0, 0, 0, 0, 2.6, 1]
        ],
    }


class TestUdpSensorFields(unittest.TestCase):
    def test_list_matches_what_parse_obs_st_provides(self) -> None:
        parsed = parse_obs_st(_obs_st_message())
        assert parsed is not None
        self.assertEqual(set(parsed) - {"time", "udp_updated_at"}, set(UDP_SENSOR_FIELDS))


class TestKeepFreshUdpFields(unittest.TestCase):
    def test_no_previous_snapshot_returns_cloud_unchanged(self) -> None:
        self.assertIs(keep_fresh_udp_fields(CLOUD_CONDITIONS, None), CLOUD_CONDITIONS)

    def test_previous_without_udp_returns_cloud_unchanged(self) -> None:
        previous = {**CLOUD_CONDITIONS, "air_temperature": 44.0}
        self.assertIs(keep_fresh_udp_fields(CLOUD_CONDITIONS, previous), CLOUD_CONDITIONS)

    def test_fresh_udp_values_are_kept(self) -> None:
        previous = {**CLOUD_CONDITIONS, "location_name": "Tower", **UDP_READING}

        merged = keep_fresh_udp_fields(CLOUD_CONDITIONS, previous)

        for field in UDP_SENSOR_FIELDS:
            self.assertEqual(merged[field], UDP_READING[field], field)
        self.assertEqual(merged["udp_updated_at"], UDP_READING["udp_updated_at"])
        # Everything else is the cloud's.
        self.assertEqual(merged["time"], CLOUD_TIME)
        self.assertEqual(merged["feels_like"], 43.0)
        self.assertEqual(merged["icon"], "cloudy")

    def test_udp_reading_just_inside_window_is_fresh(self) -> None:
        reading = {**UDP_READING, "udp_updated_at": CLOUD_TIME - UDP_FRESH_SECONDS}
        merged = keep_fresh_udp_fields(CLOUD_CONDITIONS, reading)
        self.assertEqual(merged["air_temperature"], 45.3)

    def test_stale_udp_reading_is_replaced_by_cloud(self) -> None:
        reading = {**UDP_READING, "udp_updated_at": CLOUD_TIME - UDP_FRESH_SECONDS - 1}
        self.assertIs(keep_fresh_udp_fields(CLOUD_CONDITIONS, reading), CLOUD_CONDITIONS)


class TestReplaceWith(unittest.TestCase):
    def test_builds_from_current_snapshot_and_returns_it(self) -> None:
        cache = LatestCache()
        cache.set({"a": 1})

        result = cache.replace_with(lambda current: {**(current or {}), "b": 2})

        self.assertEqual(result, {"a": 1, "b": 2})
        self.assertEqual(cache.get(), {"a": 1, "b": 2})

    def test_starts_from_none(self) -> None:
        cache = LatestCache()
        seen = []
        cache.replace_with(lambda current: seen.append(current) or {"x": 1})
        self.assertEqual(seen, [None])


class TestPollerWithUdp(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db_path)
        init_db(self.db_path)

    def _poll_once(self, cache: LatestCache) -> None:
        stop_event = threading.Event()
        raw = {
            "current_conditions": dict(CLOUD_CONDITIONS),
            "forecast": {"hourly": [], "daily": []},
            "location_name": "Tower 1",
        }

        def fake_fetch(*args, **kwargs):
            stop_event.set()
            return raw

        with patch("weatherhub.poller.fetch_tempest", side_effect=fake_fetch):
            poll_forever(
                cache, LatestCache(), self.db_path, "1", "token", "imperial", 0.01, stop_event
            )

    def test_fresh_udp_survives_a_cloud_poll_in_cache_and_history(self) -> None:
        cache = LatestCache()
        cache.update(UDP_READING)

        self._poll_once(cache)

        snapshot = cache.get()
        assert snapshot is not None
        self.assertEqual(snapshot["air_temperature"], 45.3)
        self.assertEqual(snapshot["station_pressure"], 24.95)
        self.assertEqual(snapshot["udp_updated_at"], UDP_READING["udp_updated_at"])
        self.assertEqual(snapshot["rest_updated_at"], CLOUD_TIME)
        self.assertEqual(snapshot["conditions"], "Cloudy")
        self.assertEqual(snapshot["location_name"], "Tower 1")

        rows = query_history(self.db_path, 0, 2_000_000_000)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["ts"], CLOUD_TIME)
        self.assertEqual(row["air_temperature"], 45.3)
        self.assertEqual(row["station_pressure"], 24.95)
        self.assertEqual(row["uv"], 0.01)
        self.assertEqual(row["relative_humidity"], 90.32)
        self.assertEqual(row["wind_lull"], 2.3)
        self.assertEqual(row["feels_like"], 43.0)
        self.assertEqual(row["icon"], "cloudy")

    def test_stale_udp_is_replaced_exactly_as_before(self) -> None:
        cache = LatestCache()
        cache.update({**UDP_READING, "udp_updated_at": CLOUD_TIME - 3600})

        self._poll_once(cache)

        snapshot = cache.get()
        assert snapshot is not None
        self.assertEqual(
            snapshot,
            {**CLOUD_CONDITIONS, "location_name": "Tower 1", "rest_updated_at": CLOUD_TIME},
        )
        row = query_history(self.db_path, 0, 2_000_000_000)[0]
        self.assertEqual(row["air_temperature"], 45.0)
        self.assertEqual(row["station_pressure"], 24.9)
        self.assertIsNone(row["wind_lull"])

    def test_without_udp_cloud_values_are_stored(self) -> None:
        cache = LatestCache()

        self._poll_once(cache)

        snapshot = cache.get()
        assert snapshot is not None
        self.assertNotIn("udp_updated_at", snapshot)
        self.assertEqual(snapshot["air_temperature"], 45.0)


if __name__ == "__main__":
    unittest.main()
