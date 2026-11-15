import importlib.util
import io
import logging
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from weatherhub.backfill import (
    BACKFILL_COLUMNS,
    DAY_SECONDS,
    MAX_WINDOW_SECONDS,
    MIN_WINDOW_SECONDS,
    TempestHistoryClient,
    _get_json,
    backfill,
    find_gaps,
    observation_to_row,
    plan_windows,
    start_startup_backfill,
)
from weatherhub.db import init_db, insert_observation, query_history
from weatherhub.tempest_client import TempestError

T0 = 1_790_000_100  # a multiple of 300, so fake 5-minute buckets line up


def make_obs(ts, temp_c=10.0, rain_mm=2.54):
    """A 22-field device-history obs_st array, metric like the real API."""
    return [
        ts, 1.0, 2.0, 3.0, 180, 3, 1000.0, temp_c, 55, 12000, 1.5, 300,
        0.0, 0, 0, 0, 2.6, 5, rain_mm, 0.0, rain_mm, 1,
    ]


class FakeSource:
    """Answers like the API: one 5-minute bucket per 300 s in [start, end]."""

    def __init__(self, fail_on_call=None, step=300):
        self.calls = []
        self.fail_on_call = fail_on_call
        self.step = step

    def observations(self, start, end):
        self.calls.append((start, end))
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise TempestError("Tempest API returned status 500")
        first = -(-start // self.step) * self.step
        return [make_obs(ts) for ts in range(first, end + 1, self.step)]


def no_wait(_seconds):
    return False


class DbTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db_path)
        init_db(self.db_path)

    def add_rows(self, timestamps, **values):
        for ts in timestamps:
            insert_observation(self.db_path, {"time": ts, "air_temperature": 50.0, **values})

    def all_rows(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute("SELECT * FROM observations ORDER BY ts").fetchall()
        finally:
            conn.close()


class TestObservationToRow(unittest.TestCase):
    def test_imperial_uses_app_conversions_and_precision(self):
        row = observation_to_row(make_obs(T0, temp_c=22.2, rain_mm=6.6), "imperial")
        self.assertEqual(row["ts"], T0)
        self.assertEqual(row["air_temperature"], 72.0)
        self.assertEqual(row["wind_lull"], 2.2)
        self.assertEqual(row["wind_avg"], 4.5)
        self.assertEqual(row["wind_gust"], 6.7)
        self.assertEqual(row["wind_direction"], 180)
        self.assertEqual(row["station_pressure"], 29.53)
        self.assertEqual(row["relative_humidity"], 55)
        self.assertEqual(row["brightness"], 12000)
        self.assertEqual(row["uv"], 1.5)
        self.assertEqual(row["solar_radiation"], 300)
        self.assertEqual(row["precip_accum_local_day"], 0.26)
        self.assertEqual(set(row) - {"ts"}, set(BACKFILL_COLUMNS))

    def test_metric_passes_api_values_through(self):
        row = observation_to_row(make_obs(T0, temp_c=22.2, rain_mm=6.6), "metric")
        self.assertEqual(row["air_temperature"], 22.2)
        self.assertEqual(row["wind_avg"], 2.0)
        self.assertEqual(row["station_pressure"], 1000.0)
        self.assertEqual(row["precip_accum_local_day"], 6.6)

    def test_nulls_and_short_arrays_stay_null(self):
        obs = make_obs(T0)
        obs[7] = None
        row = observation_to_row(obs[:12], "imperial")
        self.assertIsNone(row["air_temperature"])
        self.assertIsNone(row["precip_accum_local_day"])
        self.assertEqual(row["wind_avg"], 4.5)

    def test_rejects_garbage(self):
        self.assertIsNone(observation_to_row([], "imperial"))
        self.assertIsNone(observation_to_row([None, 1], "imperial"))
        self.assertIsNone(observation_to_row("nope", "imperial"))


class TestFindGaps(DbTestCase):
    def test_finds_only_gaps_longer_than_threshold(self):
        self.add_rows([T0, T0 + 300, T0 + 600, T0 + 600 + 5000, T0 + 600 + 5300])
        gaps = find_gaps(self.db_path, T0, T0 + 6000, min_gap_seconds=900)
        self.assertEqual(gaps, [(T0 + 601, T0 + 5599)])

    def test_open_edges_and_empty_range(self):
        self.assertEqual(find_gaps(self.db_path, T0, T0 + 3600, 900), [(T0, T0 + 3600)])
        self.add_rows([T0 + 1800])
        self.assertEqual(
            find_gaps(self.db_path, T0, T0 + 3600, 900),
            [(T0, T0 + 1799), (T0 + 1801, T0 + 3600)],
        )

    def test_gap_partly_outside_range_is_measured_whole_then_clipped(self):
        self.add_rows([T0, T0 + 10_000])
        self.assertEqual(
            find_gaps(self.db_path, T0 + 9_500, T0 + 20_000, 900),
            [(T0 + 9_500, T0 + 9_999), (T0 + 10_001, T0 + 20_000)],
        )


class TestPlanWindows(unittest.TestCase):
    def test_long_gap_is_split_into_bounded_windows(self):
        windows = plan_windows([(T0, T0 + 22 * DAY_SECONDS)])
        self.assertEqual(len(windows), 5)
        self.assertEqual(windows[0][0], T0)
        self.assertEqual(windows[-1][1], T0 + 22 * DAY_SECONDS)
        for start, end in windows:
            self.assertGreaterEqual(end - start, MIN_WINDOW_SECONDS)
            self.assertLessEqual(end - start, MAX_WINDOW_SECONDS)

    def test_nearby_small_gaps_share_one_request_padded_past_a_day(self):
        windows = plan_windows([(T0, T0 + 1000), (T0 + 7200, T0 + 9000)])
        self.assertEqual(windows, [(T0 + 9000 - MIN_WINDOW_SECONDS, T0 + 9000)])

    def test_far_apart_gaps_get_separate_requests(self):
        windows = plan_windows([(T0, T0 + 1000), (T0 + 10 * DAY_SECONDS, T0 + 10 * DAY_SECONDS + 1000)])
        self.assertEqual(len(windows), 2)


class TestBackfill(DbTestCase):
    def setUp(self):
        super().setUp()
        # Rows every 5 minutes either side of a 3-day hole.
        self.before = [T0 + i * 300 for i in range(12)]
        self.hole_start = self.before[-1]
        self.hole_end = self.hole_start + 3 * DAY_SECONDS + 77
        self.after = [self.hole_end + i * 300 for i in range(12)]
        self.add_rows(self.before + self.after)
        self.range = (T0, self.after[-1])

    def run_backfill(self, source=None, **kwargs):
        return backfill(
            self.db_path,
            source or FakeSource(),
            *self.range,
            "imperial",
            pause_seconds=0,
            wait=no_wait,
            **kwargs,
        )

    def test_fills_only_inside_the_gap_and_leaves_existing_rows_alone(self):
        original = self.all_rows()
        source = FakeSource()
        result = self.run_backfill(source)

        rows = query_history(self.db_path, *self.range)
        new = [r for r in rows if r["ts"] not in set(self.before + self.after)]
        self.assertEqual(result.inserted, len(new))
        self.assertGreater(len(new), 800)
        for row in new:
            self.assertGreater(row["ts"], self.hole_start + 60)
            self.assertLess(row["ts"], self.hole_end - 60)
            self.assertEqual(row["air_temperature"], 50.0)  # 10 C
            self.assertIsNone(row["feels_like"])
            self.assertIsNone(row["conditions"])
        # Every original row is byte-for-byte unchanged.
        self.assertEqual([r for r in self.all_rows() if r[0] in {o[0] for o in original}], original)
        for start, end in source.calls:
            self.assertGreater(end - start, DAY_SECONDS)
            self.assertLessEqual(end - start, MAX_WINDOW_SECONDS)

    def test_second_run_inserts_nothing(self):
        self.run_backfill()
        count = len(self.all_rows())
        result = self.run_backfill()
        self.assertEqual(result.inserted, 0)
        self.assertEqual(result.gaps, [])
        self.assertEqual(len(self.all_rows()), count)

    def test_dry_run_reports_without_writing(self):
        original = self.all_rows()
        dry = self.run_backfill(dry_run=True)
        self.assertEqual(self.all_rows(), original)
        real = self.run_backfill()
        self.assertGreater(dry.inserted, 0)
        self.assertEqual(dry.inserted, real.inserted)

    def test_never_overwrites_a_row_with_the_same_timestamp(self):
        # A stored row sitting on a 5-minute mark inside a long hole.
        mid = self.hole_start + DAY_SECONDS + 300 - (self.hole_start + DAY_SECONDS) % 300
        self.add_rows([mid], air_temperature=99.9)
        self.run_backfill()
        row = query_history(self.db_path, mid - 60, mid + 60)
        self.assertEqual([(r["ts"], r["air_temperature"]) for r in row], [(mid, 99.9)])

    def test_failure_keeps_rows_from_earlier_requests(self):
        self.range = (T0, T0 + 12 * DAY_SECONDS)
        with self.assertRaises(TempestError):
            self.run_backfill(FakeSource(fail_on_call=2))
        self.assertGreater(len(self.all_rows()), len(self.before + self.after))

    def test_stops_when_wait_says_so(self):
        self.range = (T0, T0 + 12 * DAY_SECONDS)
        source = FakeSource()
        backfill(self.db_path, source, *self.range, pause_seconds=0, wait=lambda s: True)
        self.assertEqual(len(source.calls), 1)


class TestHttp(unittest.TestCase):
    def test_error_messages_never_contain_the_token(self):
        token = "secret-token-123"

        def fail(request, timeout):
            raise urllib.error.HTTPError(
                request.full_url, 401, "Unauthorized", {}, io.BytesIO(f"bad {token}".encode())
            )

        with patch("weatherhub.backfill.urllib.request.urlopen", side_effect=fail):
            with self.assertRaises(TempestError) as ctx:
                _get_json("/stations/1", {}, token, 5)
        self.assertNotIn(token, str(ctx.exception))
        self.assertIn("401", str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)

        def unreachable(request, timeout):
            raise urllib.error.URLError(f"no route for {request.full_url}")

        with patch("weatherhub.backfill.urllib.request.urlopen", side_effect=unreachable):
            with self.assertRaises(TempestError) as ctx:
                _get_json("/stations/1", {}, token, 5)
        self.assertNotIn(token, str(ctx.exception))

    def test_device_id_picks_the_active_tempest_sensor(self):
        stations = {
            "stations": [
                {
                    "devices": [
                        {"device_id": 1, "device_type": "HB", "serial_number": "HB-1"},
                        {"device_id": 2, "device_type": "ST", "serial_number": ""},
                        {"device_id": 3, "device_type": "ST", "serial_number": "ST-3"},
                    ]
                }
            ]
        }
        with patch("weatherhub.backfill._get_json", return_value=stations) as get:
            client = TempestHistoryClient("99", "tok")
            self.assertEqual(client.device_id(), 3)
            self.assertEqual(client.device_id(), 3)
        self.assertEqual(get.call_count, 1)

        with patch("weatherhub.backfill._get_json", return_value={"stations": []}):
            with self.assertRaises(TempestError):
                TempestHistoryClient("99", "tok").device_id()

    def test_observations_checks_type(self):
        client = TempestHistoryClient("99", "tok")
        client._device_id = 3
        reply = {"status": {"status_code": 0}, "type": "obs_st", "obs": [make_obs(T0)]}
        with patch("weatherhub.backfill._get_json", return_value=reply) as get:
            self.assertEqual(client.observations(T0, T0 + 10), [make_obs(T0)])
        self.assertEqual(get.call_args[0][1], {"time_start": T0, "time_end": T0 + 10})
        with patch("weatherhub.backfill._get_json", return_value={**reply, "type": "obs_air"}):
            with self.assertRaises(TempestError):
                client.observations(T0, T0 + 10)


class TestStartupBackfill(DbTestCase):
    def start(self, source, **env):
        with patch.dict(os.environ, {"BACKFILL_ON_STARTUP": "", "BACKFILL_MAX_DAYS": "30", **env}):
            return start_startup_backfill(
                self.db_path, "99", "tok", "imperial", threading.Event(), source=source
            )

    def test_fills_missed_span_in_background(self):
        now = int(time.time())
        self.add_rows([now - 2 * DAY_SECONDS])
        source = FakeSource()
        thread = self.start(source)
        self.assertIsNotNone(thread)
        thread.join(10)
        rows = query_history(self.db_path, now - 3 * DAY_SECONDS, now)
        self.assertGreater(len(rows), 500)
        self.assertEqual(len(source.calls), 1)

    def test_bounded_to_max_days(self):
        now = int(time.time())
        self.add_rows([now - 100 * DAY_SECONDS])
        thread = self.start(FakeSource(), BACKFILL_MAX_DAYS="3")
        thread.join(10)
        rows = query_history(self.db_path, now - 100 * DAY_SECONDS + 1, now)
        self.assertGreater(len(rows), 500)
        self.assertGreaterEqual(min(r["ts"] for r in rows), now - 3 * DAY_SECONDS - 1)

    def test_nothing_to_do(self):
        self.assertIsNone(self.start(FakeSource()))  # empty database
        self.add_rows([int(time.time()) - 60])
        self.assertIsNone(self.start(FakeSource()))  # up to date

    def test_can_be_turned_off(self):
        self.add_rows([int(time.time()) - 5 * DAY_SECONDS])
        source = FakeSource()
        self.assertIsNone(self.start(source, BACKFILL_ON_STARTUP="false"))
        self.assertEqual(source.calls, [])

    def test_failure_only_logs(self):
        self.add_rows([int(time.time()) - DAY_SECONDS])
        with self.assertLogs("weatherhub", level=logging.ERROR) as logs:
            thread = self.start(FakeSource(fail_on_call=1))
            thread.join(10)
        self.assertTrue(any("Startup backfill failed" in line for line in logs.output))


class TestCliDates(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parent.parent / "scripts" / "backfill_history.py"
        spec = importlib.util.spec_from_file_location("backfill_history", path)
        self.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cli)

    def test_dates_are_utc_and_date_only_end_covers_the_day(self):
        self.assertEqual(self.cli._parse_when("2026-10-23", is_end=False), 1792713600)
        self.assertEqual(self.cli._parse_when("2026-10-23", is_end=True), 1792713600 + 86399)
        self.assertEqual(self.cli._parse_when("2026-10-23T06:00", is_end=True), 1792713600 + 21600)
        self.assertEqual(self.cli._parse_when("2026-10-23T00:00-06:00", is_end=False), 1792713600 + 21600)


if __name__ == "__main__":
    unittest.main()
