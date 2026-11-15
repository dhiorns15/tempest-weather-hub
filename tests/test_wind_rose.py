import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from weatherhub.cache import LatestCache
from weatherhub.db import init_db, insert_observation
from weatherhub.server import make_server
from weatherhub.station_timezone import StationTimezone
from weatherhub.wind_rose import SECTORS, speed_classes, wind_rose


class WindRoseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db_path)
        init_db(self.db_path)
        self.next_ts = 1_790_000_000

    def add(self, wind_avg, wind_direction, ts=None) -> int:
        if ts is None:
            ts = self.next_ts
            self.next_ts += 60
        insert_observation(
            self.db_path, {"time": ts, "wind_avg": wind_avg, "wind_direction": wind_direction}
        )
        return ts

    def rose(self, unit_system="imperial"):
        return wind_rose(self.db_path, 0, 2_000_000_000, unit_system)


class TestWindRose(WindRoseTestCase):
    def test_empty_database(self) -> None:
        rose = self.rose()

        self.assertEqual(rose["total"], 0)
        self.assertEqual(rose["calm"], 0)
        self.assertIsNone(rose["calm_share"])
        self.assertEqual(rose["sectors"], list(SECTORS))
        self.assertEqual(len(rose["counts"]), 16)
        self.assertTrue(all(row == [0] * len(rose["speed_classes"]) for row in rose["counts"]))

    def test_sector_boundaries(self) -> None:
        cases = {
            0: "N", 11.24: "N", 11.25: "NNE", 33.74: "NNE", 33.75: "NE", 90: "E",
            180: "S", 270: "W", 348.74: "NNW", 348.75: "N", 359: "N", 360: "N",
        }  # fmt: skip
        for direction, expected in cases.items():
            with self.subTest(direction=direction):
                fd, path = tempfile.mkstemp(suffix=".db")
                os.close(fd)
                self.addCleanup(os.remove, path)
                init_db(path)
                insert_observation(path, {"time": 1, "wind_avg": 5.0, "wind_direction": direction})
                rose = wind_rose(path, 0, 10)
                sector = next(i for i, row in enumerate(rose["counts"]) if sum(row))
                self.assertEqual(rose["sectors"][sector], expected)

    def test_speed_classes_and_calm(self) -> None:
        for speed in (0.0, 0.99, 1.0, 2.99, 3.0, 4.99, 5.0, 9.99, 10.0, 15.0, 19.99, 20.0, 45.0):
            self.add(speed, 90)

        rose = self.rose()
        east = rose["counts"][SECTORS.index("E")]

        self.assertEqual(rose["calm"], 2)
        # 1-3, 3-5, 5-10, 10-15, 15-20, 20+
        self.assertEqual(east, [2, 2, 2, 1, 2, 2])
        self.assertEqual(rose["total"], 13)
        self.assertAlmostEqual(rose["calm_share"], 2 / 13)

    def test_calm_has_no_direction_and_bad_readings_are_left_out(self) -> None:
        self.add(0.0, 0)  # still air, reported as 0 degrees
        self.add(0.4, None)  # calm with no direction is still calm
        self.add(3.5, None)  # moving air with no direction: not usable
        self.add(None, 180)  # no speed: not a wind reading
        self.add(2.0, -5)  # nonsense bearing
        self.add(2.0, 180)

        rose = self.rose()

        self.assertEqual(rose["calm"], 2)
        self.assertEqual(rose["counts"][SECTORS.index("N")], [0] * 6)
        self.assertEqual(rose["counts"][SECTORS.index("S")], [1, 0, 0, 0, 0, 0])
        self.assertEqual(rose["total"], 3)

    def test_totals_add_up(self) -> None:
        speeds = [0.2, 1.5, 3.3, 7.0, 12.0, 17.0, 25.0]
        for i in range(200):
            self.add(speeds[i % len(speeds)], (i * 37) % 360)

        rose = self.rose()

        self.assertEqual(rose["total"], 200)
        self.assertEqual(rose["calm"] + sum(map(sum, rose["counts"])), rose["total"])

    def test_range_is_inclusive_and_bounded(self) -> None:
        first = self.add(2.0, 90)
        second = self.add(2.0, 90)
        self.add(2.0, 90)

        rose = wind_rose(self.db_path, first, second)

        self.assertEqual(rose["total"], 2)
        self.assertEqual((rose["start"], rose["end"]), (first, second))

    def test_dst_day_counts_every_reading(self) -> None:
        # The station-local fall-back day (Denver, 1 Nov 2026) is 25 hours:
        # an hourly reading gives 25 of them.
        zone = ZoneInfo("America/Denver")
        start = int(datetime(2026, 11, 1, tzinfo=zone).timestamp())
        end = int(datetime(2026, 11, 2, tzinfo=zone).timestamp()) - 1
        for ts in range(start, end + 1, 3600):
            self.add(4.0, 225, ts)

        rose = wind_rose(self.db_path, start, end)

        self.assertEqual(rose["total"], 25)
        self.assertEqual(rose["counts"][SECTORS.index("SW")], [0, 25, 0, 0, 0, 0])

    def test_imperial_classes(self) -> None:
        calm_below, classes = speed_classes("imperial")

        self.assertEqual(calm_below, 1.0)
        self.assertEqual(
            [c["label"] for c in classes],
            ["1–3 mph", "3–5 mph", "5–10 mph", "10–15 mph", "15–20 mph", "20+ mph"],
        )
        self.assertEqual((classes[0]["min"], classes[0]["max"]), (1.0, 3.0))
        self.assertIsNone(classes[-1]["max"])

    def test_metric_classes(self) -> None:
        self.add(0.45, 90)  # calm in m/s
        self.add(0.5, 90)
        self.add(12.0, 90)

        rose = self.rose("metric")

        self.assertEqual(rose["unit"], "m/s")
        self.assertEqual(rose["calm_below"], 0.5)
        self.assertEqual(
            [c["label"] for c in rose["speed_classes"]],
            ["0.5–1.5 m/s", "1.5–2.5 m/s", "2.5–5 m/s", "5–7.5 m/s", "7.5–10 m/s", "10+ m/s"],
        )
        self.assertEqual(rose["calm"], 1)
        self.assertEqual(rose["counts"][SECTORS.index("E")], [1, 0, 0, 0, 0, 1])

    def test_unknown_unit_system_is_imperial(self) -> None:
        self.assertEqual(speed_classes("furlongs"), speed_classes("imperial"))


class TestSiteEndpoints(unittest.TestCase):
    """/site/wind-rose and /site/records over HTTP."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db_path)
        init_db(self.db_path)
        static_dir = Path(tempfile.mkdtemp())
        (static_dir / "index.html").write_text("<h1>home</h1>", encoding="utf-8")

        self.server = make_server(
            LatestCache(),
            LatestCache(),
            LatestCache(),
            self.db_path,
            static_dir,
            port=0,
            cloud_configured=False,
            udp_configured=True,
            unit_system="metric",
            station_timezone=StationTimezone(override="America/Denver"),
        )
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path):
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_wind_rose_endpoint(self) -> None:
        insert_observation(self.db_path, {"time": 1000, "wind_avg": 3.0, "wind_direction": 200})
        insert_observation(self.db_path, {"time": 1060, "wind_avg": 0.1, "wind_direction": 0})

        status, body = self.get("/site/wind-rose?start=900&end=2000")

        self.assertEqual(status, 200)
        self.assertEqual(body["unit"], "m/s")
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["calm"], 1)
        self.assertEqual(body["counts"][SECTORS.index("SSW")], [0, 0, 1, 0, 0, 0])

    def test_wind_rose_defaults_to_last_24_hours(self) -> None:
        status, body = self.get("/site/wind-rose")

        self.assertEqual(status, 200)
        self.assertEqual(body["end"] - body["start"], 86400)
        self.assertEqual(body["total"], 0)

    def test_wind_rose_rejects_bad_ranges(self) -> None:
        self.assertEqual(self.get("/site/wind-rose?start=abc&end=10")[0], 400)
        self.assertEqual(self.get("/site/wind-rose?start=10&end=10")[0], 400)
        self.assertEqual(self.get("/site/wind-rose?start=20&end=10")[0], 400)

    def test_records_endpoint(self) -> None:
        ts = int(time.time()) - 3 * 86400
        insert_observation(self.db_path, {"time": ts, "air_temperature": 12.5})

        status, body = self.get("/site/records")

        self.assertEqual(status, 200)
        self.assertEqual(body["timezone"], "America/Denver")
        self.assertEqual(body["units"]["temp"], "°C")
        self.assertEqual(body["records"]["all_time"]["temp_high"]["value"], 12.5)
        self.assertEqual(body["records"]["all_time"]["temp_high"]["ts"], ts)
        self.assertEqual(len(body["on_this_day"]["comparisons"]), 4)

    def test_records_endpoint_on_empty_database(self) -> None:
        status, body = self.get("/site/records")

        self.assertEqual(status, 200)
        self.assertIsNone(body["first_day"])
        self.assertIsNone(body["records"]["all_time"]["temp_high"])


if __name__ == "__main__":
    unittest.main()
