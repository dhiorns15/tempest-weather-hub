import os
import tempfile
import unittest

from weatherhub.db import init_db, insert_observation, query_history


class TestDb(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db_path)
        init_db(self.db_path)

    def test_insert_and_query_raw(self) -> None:
        insert_observation(
            self.db_path,
            {
                "time": 1_700_000_000,
                "air_temperature": 21.5,
                "relative_humidity": 55,
                "wind_avg": 3.2,
                "wind_direction": 180,
                "wind_gust": 5.1,
                "station_pressure": 29.9,
                "precip_accum_local_day": 0,
                "uv": 2,
                "solar_radiation": 400,
                "conditions": "Clear",
                "icon": "clear-day",
            },
        )

        rows = query_history(self.db_path, 0, 2_000_000_000)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ts"], 1_700_000_000)
        self.assertEqual(rows[0]["air_temperature"], 21.5)
        self.assertEqual(rows[0]["conditions"], "Clear")

    def test_query_range_excludes_out_of_bounds_rows(self) -> None:
        insert_observation(self.db_path, {"time": 100, "air_temperature": 10})
        insert_observation(self.db_path, {"time": 200, "air_temperature": 20})
        insert_observation(self.db_path, {"time": 300, "air_temperature": 30})

        rows = query_history(self.db_path, 150, 250)

        self.assertEqual([r["ts"] for r in rows], [200])

    def test_insert_without_time_is_ignored(self) -> None:
        insert_observation(self.db_path, {"air_temperature": 10})

        rows = query_history(self.db_path, 0, 2_000_000_000)

        self.assertEqual(rows, [])

    def test_reinserting_same_timestamp_replaces_row(self) -> None:
        insert_observation(self.db_path, {"time": 100, "air_temperature": 10})
        insert_observation(self.db_path, {"time": 100, "air_temperature": 99})

        rows = query_history(self.db_path, 0, 2_000_000_000)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["air_temperature"], 99)

    def test_hourly_resolution_aggregates(self) -> None:
        base = 1_700_000_000 - (1_700_000_000 % 3600)  # align to an hour boundary
        insert_observation(self.db_path, {"time": base, "air_temperature": 10})
        insert_observation(self.db_path, {"time": base + 600, "air_temperature": 20})
        insert_observation(self.db_path, {"time": base + 3600, "air_temperature": 30})

        rows = query_history(self.db_path, base, base + 3600, resolution="hourly")

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["air_temperature_avg"], 15)
        self.assertEqual(rows[0]["air_temperature_min"], 10)
        self.assertEqual(rows[0]["air_temperature_max"], 20)
        self.assertEqual(rows[1]["air_temperature_avg"], 30)


if __name__ == "__main__":
    unittest.main()
