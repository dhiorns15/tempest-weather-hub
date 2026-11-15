import json
import os
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from weatherhub.cache import LatestCache
from weatherhub.db import history_columns, init_db, insert_observation, query_history
from weatherhub.poller import poll_forever
from weatherhub.server import make_server
from weatherhub.station_timezone import StationTimezone, is_valid_timezone

DENVER = ZoneInfo("America/Denver")


def _local_midnight(year: int, month: int, day: int, zone: ZoneInfo = DENVER) -> int:
    return int(datetime(year, month, day, tzinfo=zone).timestamp())


class _DbTestCase(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db_path)
        init_db(self.db_path)


class TestStationTimezone(_DbTestCase):
    def test_defaults_to_utc(self) -> None:
        self.assertEqual(StationTimezone(self.db_path).name, "UTC")
        self.assertEqual(StationTimezone().name, "UTC")

    def test_reported_zone_is_used_and_persisted(self) -> None:
        tz = StationTimezone(self.db_path)
        tz.set_reported("America/Denver")
        self.assertEqual(tz.name, "America/Denver")

        # A fresh instance (i.e. after a restart, before any poll) remembers it.
        self.assertEqual(StationTimezone(self.db_path).name, "America/Denver")

    def test_override_beats_reported(self) -> None:
        StationTimezone(self.db_path).set_reported("America/Denver")
        tz = StationTimezone(self.db_path, "Europe/London")
        self.assertEqual(tz.name, "Europe/London")
        tz.set_reported("America/Chicago")
        self.assertEqual(tz.name, "Europe/London")

    def test_invalid_override_logs_and_falls_back(self) -> None:
        StationTimezone(self.db_path).set_reported("America/Denver")
        with self.assertLogs("weatherhub", level="ERROR") as logs:
            tz = StationTimezone(self.db_path, "Mars/Olympus_Mons")
        self.assertIn("STATION_TIMEZONE", logs.output[0])
        self.assertEqual(tz.name, "America/Denver")

        with self.assertLogs("weatherhub", level="ERROR"):
            self.assertEqual(StationTimezone(None, "not a zone").name, "UTC")

    def test_invalid_reported_values_are_ignored(self) -> None:
        tz = StationTimezone(self.db_path)
        for bad in (None, "", 42, "Nowhere/Special", "../etc/passwd", "America"):
            tz.set_reported(bad)
        self.assertEqual(tz.name, "UTC")
        self.assertEqual(StationTimezone(self.db_path).name, "UTC")

    def test_is_valid_timezone(self) -> None:
        self.assertTrue(is_valid_timezone("America/Denver"))
        self.assertTrue(is_valid_timezone("UTC"))
        self.assertFalse(is_valid_timezone("America/Atlantis"))
        self.assertFalse(is_valid_timezone(None))


class TestPollerRecordsTimezone(_DbTestCase):
    def test_poll_records_cloud_reported_timezone(self) -> None:
        stop_event = threading.Event()
        raw = {
            "current_conditions": {"time": 1_794_712_717, "air_temperature": 45.0},
            "forecast": {},
            "location_name": "Tower",
            "timezone": "America/Denver",
            "timezone_offset_minutes": -420,
        }

        def fake_fetch(*args, **kwargs):
            stop_event.set()
            return raw

        tz = StationTimezone(self.db_path)
        with patch("weatherhub.poller.fetch_tempest", side_effect=fake_fetch):
            poll_forever(
                LatestCache(),
                LatestCache(),
                self.db_path,
                "1",
                "token",
                "imperial",
                0.01,
                stop_event,
                station_timezone=tz,
            )

        self.assertEqual(tz.name, "America/Denver")
        self.assertEqual(StationTimezone(self.db_path).name, "America/Denver")


class TestLocalDailyBuckets(_DbTestCase):
    def _insert_half_hourly(self, start: int, end: int) -> None:
        # air_temperature carries the row's ts, so a bucket's min/max show
        # exactly which instants it covers.
        for ts in range(start, end, 1800):
            insert_observation(self.db_path, {"time": ts, "air_temperature": ts})

    def _buckets(self, start: int, end: int, tz_name: str) -> dict[str, dict]:
        rows = query_history(self.db_path, start, end, "daily", tz_name)
        return {row["bucket"]: row for row in rows}

    def test_fall_back_day_spans_25_hours(self) -> None:
        start, end = _local_midnight(2026, 10, 30), _local_midnight(2026, 11, 4)
        self._insert_half_hourly(start, end)

        buckets = self._buckets(start, end, "America/Denver")

        self.assertEqual(
            list(buckets),
            [f"2026-{m:02d}-{d:02d}T00:00:00Z" for m, d in ((10, 30), (10, 31), (11, 1), (11, 2), (11, 3))],
        )
        nov1 = buckets["2026-11-01T00:00:00Z"]
        self.assertEqual(nov1["air_temperature_min"], _local_midnight(2026, 11, 1))
        self.assertEqual(nov1["air_temperature_max"], _local_midnight(2026, 11, 2) - 1800)
        self.assertEqual(_local_midnight(2026, 11, 2) - _local_midnight(2026, 11, 1), 25 * 3600)

        oct31 = buckets["2026-10-31T00:00:00Z"]
        self.assertEqual(oct31["air_temperature_max"] - oct31["air_temperature_min"], 23.5 * 3600)

    def test_spring_forward_day_spans_23_hours(self) -> None:
        start, end = _local_midnight(2026, 3, 7), _local_midnight(2026, 3, 10)
        self._insert_half_hourly(start, end)

        mar8 = self._buckets(start, end, "America/Denver")["2026-03-08T00:00:00Z"]

        self.assertEqual(mar8["air_temperature_min"], _local_midnight(2026, 3, 8))
        self.assertEqual(mar8["air_temperature_max"], _local_midnight(2026, 3, 9) - 1800)
        self.assertEqual(mar8["air_temperature_max"] - mar8["air_temperature_min"], 22.5 * 3600)

    def test_positive_half_hour_offset_zone(self) -> None:
        kolkata = ZoneInfo("Asia/Kolkata")
        start, end = _local_midnight(2026, 6, 1, kolkata), _local_midnight(2026, 6, 3, kolkata)
        self._insert_half_hourly(start, end)

        buckets = self._buckets(start, end, "Asia/Kolkata")

        self.assertEqual(list(buckets), ["2026-06-01T00:00:00Z", "2026-06-02T00:00:00Z"])
        self.assertEqual(buckets["2026-06-01T00:00:00Z"]["air_temperature_min"], start)

    def test_utc_matches_plain_utc_dates(self) -> None:
        start = _local_midnight(2026, 10, 31, ZoneInfo("UTC"))
        self._insert_half_hourly(start, start + 2 * 86400)

        buckets = self._buckets(start, start + 2 * 86400, "UTC")
        self.assertEqual(list(buckets), ["2026-10-31T00:00:00Z", "2026-11-01T00:00:00Z"])
        self.assertEqual(buckets["2026-10-31T00:00:00Z"]["air_temperature_max"], start + 86400 - 1800)
        # The default is UTC, as before.
        self.assertEqual(
            query_history(self.db_path, start, start + 2 * 86400, "daily"),
            query_history(self.db_path, start, start + 2 * 86400, "daily", "UTC"),
        )

    def test_empty_range(self) -> None:
        self.assertEqual(query_history(self.db_path, 0, 100, "daily", "America/Denver"), [])

    def test_hourly_stays_utc_hours(self) -> None:
        base = _local_midnight(2026, 10, 31, ZoneInfo("UTC"))
        self._insert_half_hourly(base, base + 7201)
        rows = query_history(self.db_path, base, base + 7200, "hourly", "America/Denver")
        self.assertEqual(
            [row["bucket"] for row in rows],
            ["2026-10-31T00:00:00Z", "2026-10-31T01:00:00Z", "2026-10-31T02:00:00Z"],
        )


class TestServerTimezone(_DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.static_dir = Path(tempfile.mkdtemp())
        (self.static_dir / "index.html").write_text("<h1>home</h1>", encoding="utf-8")
        self.tz = StationTimezone(self.db_path)
        self.tz.set_reported("America/Denver")
        server = make_server(
            LatestCache(),
            LatestCache(),
            LatestCache(),
            self.db_path,
            self.static_dir,
            port=0,
            cloud_configured=True,
            udp_configured=True,
            station_timezone=self.tz,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, timeout=5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def _get(self, path: str) -> bytes:
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
            return response.read()

    def test_capabilities_reports_timezone(self) -> None:
        body = json.loads(self._get("/api/capabilities"))
        self.assertEqual(body["timezone"], "America/Denver")

    def test_history_json_reports_timezone_and_local_days(self) -> None:
        # 6 PM local on Nov 13 is already Nov 14 in UTC.
        evening = int(datetime(2026, 11, 13, 18, 0, tzinfo=DENVER).timestamp())
        insert_observation(self.db_path, {"time": evening, "air_temperature": 40})

        body = json.loads(
            self._get(f"/site/history?start={evening - 3600}&end={evening + 3600}&resolution=daily")
        )

        self.assertEqual(body["timezone"], "America/Denver")
        self.assertEqual([row["bucket"] for row in body["observations"]], ["2026-11-13T00:00:00Z"])

        raw = json.loads(
            self._get(f"/site/history?start={evening - 3600}&end={evening + 3600}&resolution=raw")
        )
        self.assertEqual(raw["timezone"], "America/Denver")

    def test_csv_columns_unchanged(self) -> None:
        body = self._get("/site/history?start=0&end=100&resolution=daily&format=csv")
        self.assertEqual(body.decode("utf-8").strip(), ",".join(history_columns("daily")))


if __name__ == "__main__":
    unittest.main()
