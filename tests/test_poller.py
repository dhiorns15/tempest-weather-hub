import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from weatherhub.cache import LatestCache
from weatherhub.db import init_db, query_history
from weatherhub.poller import poll_forever
from weatherhub.tempest_client import TempestError


class TestPollForever(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db_path)
        init_db(self.db_path)

    def test_successful_poll_updates_cache_and_history(self) -> None:
        cache = LatestCache()
        forecast_cache = LatestCache()
        stop_event = threading.Event()

        raw = {
            "current_conditions": {
                "time": 1_700_000_000,
                "air_temperature": 21.5,
                "conditions": "Clear",
            },
            "forecast": {"hourly": [{"time": 1_700_003_600}], "daily": []},
            "location_name": "Tower 1",
        }

        def fake_fetch(*args, **kwargs):
            stop_event.set()
            return raw

        with patch("weatherhub.poller.fetch_tempest", side_effect=fake_fetch):
            poll_forever(
                cache, forecast_cache, self.db_path, "1234", "token", "metric", 0.01, stop_event
            )

        snapshot = cache.get()
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot["air_temperature"], 21.5)
        self.assertEqual(snapshot["location_name"], "Tower 1")
        self.assertEqual(snapshot["rest_updated_at"], 1_700_000_000)

        forecast_snapshot = forecast_cache.get()
        self.assertIsNotNone(forecast_snapshot)
        assert forecast_snapshot is not None
        self.assertEqual(len(forecast_snapshot["hourly"]), 1)

        rows = query_history(self.db_path, 0, 2_000_000_000)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ts"], 1_700_000_000)

    def test_failed_poll_keeps_previous_cache_and_adds_no_row(self) -> None:
        cache = LatestCache()
        forecast_cache = LatestCache()
        previous_snapshot = {"air_temperature": 99, "time": 1}
        cache.set(previous_snapshot)
        stop_event = threading.Event()

        def failing_fetch(*args, **kwargs):
            stop_event.set()
            raise TempestError("boom")

        with patch("weatherhub.poller.fetch_tempest", side_effect=failing_fetch):
            poll_forever(
                cache, forecast_cache, self.db_path, "1234", "token", "metric", 0.01, stop_event
            )

        self.assertEqual(cache.get(), previous_snapshot)
        self.assertIsNone(forecast_cache.get())
        self.assertEqual(query_history(self.db_path, 0, 2_000_000_000), [])


if __name__ == "__main__":
    unittest.main()
