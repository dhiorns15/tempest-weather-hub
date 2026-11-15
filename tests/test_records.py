import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from weatherhub.db import init_db, insert_observation
from weatherhub.records import RecordsCache, _months_back, build_records

DENVER = ZoneInfo("America/Denver")


def local_ts(year, month, day, hour=12, minute=0, second=0, zone=DENVER):
    return int(datetime(year, month, day, hour, minute, second, tzinfo=zone).timestamp())


class RecordsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db_path)
        init_db(self.db_path)

    def add(self, ts, **fields) -> None:
        insert_observation(self.db_path, {"time": ts, **fields})

    def build(self, now, tz_name="America/Denver"):
        conn = sqlite3.connect(self.db_path)
        try:
            payload, _ = build_records(conn, tz_name, now)
            return payload
        finally:
            conn.close()


class TestEmptyDatabase(RecordsTestCase):
    def test_empty_database(self) -> None:
        payload = RecordsCache().get(self.db_path, "America/Denver", now=local_ts(2026, 11, 14))

        self.assertIsNone(payload["first_day"])
        self.assertEqual(payload["days_with_data"], 0)
        self.assertEqual(payload["today"], "2026-11-14")
        for scope in payload["records"].values():
            self.assertEqual(scope["days"], 0)
            for key in ("temp_high", "temp_low", "gust", "wettest_day", "pressure_high", "pressure_low"):
                self.assertIsNone(scope[key])
            self.assertFalse(scope["rain_reported"])
        self.assertFalse(payload["on_this_day"]["today"]["available"])
        self.assertEqual(
            [(c["key"], c["available"], c["reason"]) for c in payload["on_this_day"]["comparisons"]],
            [
                ("yesterday", False, "before_history"),
                ("week", False, "before_history"),
                ("month", False, "before_history"),
                ("year", False, "before_history"),
            ],
        )


class TestRecords(RecordsTestCase):
    def setUp(self) -> None:
        super().setUp()
        # Last year, this year before this month, and this month.
        self.add(local_ts(2025, 7, 1, 15), air_temperature=99.0, wind_gust=30.0, station_pressure=25.1)
        self.add(local_ts(2025, 12, 20, 6), air_temperature=-5.0, wind_gust=10.0, station_pressure=24.6)
        self.add(local_ts(2026, 6, 10, 14), air_temperature=95.0, wind_gust=22.0, station_pressure=25.0)
        self.add(local_ts(2026, 6, 10, 16), air_temperature=95.0)  # equals, later the same day
        self.add(local_ts(2026, 6, 11, 14), air_temperature=95.0)  # equals, a later day
        self.add(local_ts(2026, 11, 3, 5), air_temperature=20.0, wind_gust=12.0, station_pressure=24.8)
        self.add(local_ts(2026, 11, 13, 13), air_temperature=50.0, wind_gust=8.0, station_pressure=24.9)
        self.add(local_ts(2026, 11, 14, 9), air_temperature=40.0, wind_gust=6.0, station_pressure=24.95)
        self.now = local_ts(2026, 11, 14, 10)

    def test_scopes_follow_local_year_and_month(self) -> None:
        records = self.build(self.now)["records"]

        self.assertEqual(records["all_time"]["temp_high"]["value"], 99.0)
        self.assertEqual(records["all_time"]["temp_high"]["date"], "2025-07-01")
        self.assertEqual(records["all_time"]["temp_low"]["value"], -5.0)
        self.assertEqual(records["all_time"]["gust"]["value"], 30.0)
        self.assertEqual(records["all_time"]["pressure_low"]["value"], 24.6)

        self.assertEqual(records["this_year"]["start"], "2026-01-01")
        self.assertEqual(records["this_year"]["temp_high"]["value"], 95.0)
        self.assertEqual(records["this_year"]["temp_low"]["value"], 20.0)
        self.assertEqual(records["this_year"]["gust"]["value"], 22.0)

        self.assertEqual(records["this_month"]["start"], "2026-11-01")
        self.assertEqual(records["this_month"]["days"], 3)
        self.assertEqual(records["this_month"]["temp_high"]["value"], 50.0)
        self.assertEqual(records["this_month"]["temp_high"]["date"], "2026-11-13")
        self.assertEqual(records["this_month"]["temp_low"]["value"], 20.0)
        self.assertEqual(records["this_month"]["pressure_high"]["value"], 24.95)

    def test_record_moment_is_first_time_the_value_was_reached(self) -> None:
        high = self.build(self.now)["records"]["this_year"]["temp_high"]
        # Equalled later that day and the next day; the record dates from
        # the first time.
        self.assertEqual(high["date"], "2026-06-10")
        self.assertEqual(high["ts"], local_ts(2026, 6, 10, 14))

    def test_today_counts_towards_records_straight_away(self) -> None:
        cache = RecordsCache(ttl_seconds=3600)
        cache.get(self.db_path, "America/Denver", now=self.now)
        self.add(local_ts(2026, 11, 14, 9, 30), wind_gust=41.0)

        gust = cache.get(self.db_path, "America/Denver", now=self.now + 60)["records"]["all_time"]["gust"]

        self.assertEqual(gust, {"value": 41.0, "date": "2026-11-14", "ts": local_ts(2026, 11, 14, 9, 30)})

    def test_past_days_are_cached_until_expiry(self) -> None:
        cache = RecordsCache(ttl_seconds=600)
        cache.get(self.db_path, "America/Denver", now=self.now)
        # A row backfilled into an earlier day...
        self.add(local_ts(2026, 3, 1, 4), air_temperature=-20.0)

        cached = cache.get(self.db_path, "America/Denver", now=self.now + 60)
        expired = cache.get(self.db_path, "America/Denver", now=self.now + 601)

        # ...isn't seen while the cached pass is fresh, and is once it expires.
        self.assertEqual(cached["records"]["all_time"]["temp_low"]["value"], -5.0)
        self.assertEqual(expired["records"]["all_time"]["temp_low"]["value"], -20.0)

    def test_past_days_are_rebuilt_on_a_new_local_day(self) -> None:
        cache = RecordsCache(ttl_seconds=3600)
        first = cache.get(self.db_path, "America/Denver", now=self.now)
        tomorrow = cache.get(self.db_path, "America/Denver", now=local_ts(2026, 11, 15, 0, 5))

        self.assertEqual(first["today"], "2026-11-14")
        self.assertEqual(tomorrow["today"], "2026-11-15")
        self.assertEqual(tomorrow["on_this_day"]["comparisons"][0]["date"], "2026-11-14")
        self.assertTrue(tomorrow["on_this_day"]["comparisons"][0]["available"])

    def test_on_this_day_comparisons(self) -> None:
        otd = self.build(self.now)["on_this_day"]
        today = otd["today"]
        comparisons = {c["key"]: c for c in otd["comparisons"]}

        self.assertTrue(today["available"])
        self.assertEqual((today["temp_high"], today["temp_low"], today["gust"]), (40.0, 40.0, 6.0))
        self.assertFalse(today["complete"])

        self.assertEqual(comparisons["yesterday"]["date"], "2026-11-13")
        self.assertTrue(comparisons["yesterday"]["available"])
        self.assertEqual(comparisons["yesterday"]["temp_high"], 50.0)
        # Only one reading at 1 PM - a partial day.
        self.assertFalse(comparisons["yesterday"]["complete"])

        # Inside the history, but nothing recorded that day.
        self.assertEqual(comparisons["week"]["date"], "2026-11-07")
        self.assertEqual((comparisons["week"]["available"], comparisons["week"]["reason"]), (False, "no_data"))
        self.assertEqual(comparisons["month"]["date"], "2026-10-14")
        self.assertEqual(comparisons["month"]["reason"], "no_data")
        self.assertEqual(comparisons["year"]["date"], "2025-11-14")
        self.assertEqual(comparisons["year"]["reason"], "no_data")

    def test_before_history_reason(self) -> None:
        payload = self.build(local_ts(2025, 7, 1, 18))  # first day of the history

        self.assertEqual(payload["first_day"], "2025-07-01")
        self.assertEqual(payload["days_with_data"], 1)
        self.assertEqual(
            {c["reason"] for c in payload["on_this_day"]["comparisons"]}, {"before_history"}
        )

    def test_complete_day(self) -> None:
        for hour in range(0, 24):
            self.add(local_ts(2026, 11, 12, hour, 20), air_temperature=30.0 + hour)
        week = {c["key"]: c for c in self.build(local_ts(2026, 11, 19))["on_this_day"]["comparisons"]}["week"]

        self.assertTrue(week["available"])
        self.assertTrue(week["complete"])
        self.assertEqual(week["readings"], 24)
        self.assertEqual((week["temp_low"], week["temp_high"]), (30.0, 53.0))


class TestRain(RecordsTestCase):
    def test_wettest_day_is_the_days_maximum(self) -> None:
        for hour, total in ((1, 0.0), (8, 0.12), (9, 0.30), (15, 0.27), (23, 0.28)):
            self.add(local_ts(2026, 9, 20, hour), precip_accum_local_day=total)
        for hour, total in ((6, 0.05), (20, 0.10)):
            self.add(local_ts(2026, 9, 21, hour), precip_accum_local_day=total)

        all_time = self.build(local_ts(2026, 9, 22))["records"]["all_time"]

        self.assertTrue(all_time["rain_reported"])
        self.assertEqual(all_time["wettest_day"], {"value": 0.30, "date": "2026-09-20"})

    def test_stale_total_just_after_midnight_is_ignored(self) -> None:
        # The cloud's first reading of 7 Sep still carried 6 Sep's 0.05 in.
        self.add(local_ts(2026, 9, 6, 23, 55), precip_accum_local_day=0.05)
        self.add(local_ts(2026, 9, 7, 0, 0, 4), precip_accum_local_day=0.05)
        self.add(local_ts(2026, 9, 7, 0, 5, 5), precip_accum_local_day=0.0)
        self.add(local_ts(2026, 9, 7, 23, 55), precip_accum_local_day=0.0)

        payload = self.build(local_ts(2026, 9, 8))
        yesterday = payload["on_this_day"]["comparisons"][0]

        self.assertEqual(yesterday["date"], "2026-09-07")
        self.assertEqual(yesterday["rain"], 0.0)
        self.assertEqual(payload["records"]["all_time"]["wettest_day"], {"value": 0.05, "date": "2026-09-06"})

    def test_rain_after_the_grace_period_counts(self) -> None:
        self.add(local_ts(2026, 9, 7, 0, 3), precip_accum_local_day=0.02)
        self.add(local_ts(2026, 9, 7, 0, 20), precip_accum_local_day=0.04)

        wettest = self.build(local_ts(2026, 9, 8))["records"]["all_time"]["wettest_day"]

        self.assertEqual(wettest, {"value": 0.04, "date": "2026-09-07"})

    def test_no_rain_data_at_all(self) -> None:
        # UDP-only: rows, but never a rain total.
        self.add(local_ts(2026, 9, 7, 12), air_temperature=60.0)

        all_time = self.build(local_ts(2026, 9, 8))["records"]["all_time"]

        self.assertFalse(all_time["rain_reported"])
        self.assertIsNone(all_time["wettest_day"])

    def test_dry_spell_has_no_wettest_day(self) -> None:
        self.add(local_ts(2026, 9, 7, 12), precip_accum_local_day=0.0)

        all_time = self.build(local_ts(2026, 9, 8))["records"]["all_time"]

        self.assertTrue(all_time["rain_reported"])
        self.assertIsNone(all_time["wettest_day"])


class TestLocalDays(RecordsTestCase):
    def test_fall_back_day_is_25_hours(self) -> None:
        # 1 Nov 2026 in Denver runs 06:00 UTC 1 Nov to 07:00 UTC 2 Nov.
        late = local_ts(2026, 11, 1, 23, 30)  # MST by then: 06:30 UTC on 2 Nov
        self.assertEqual(datetime.fromtimestamp(late, ZoneInfo("UTC")).strftime("%d %H:%M"), "02 06:30")
        self.add(local_ts(2026, 11, 1, 0, 10), air_temperature=40.0)
        self.add(late, air_temperature=70.0)
        self.add(local_ts(2026, 11, 2, 0, 30), air_temperature=80.0)

        payload = self.build(local_ts(2026, 11, 2, 12))
        yesterday = payload["on_this_day"]["comparisons"][0]
        high = payload["records"]["all_time"]["temp_high"]

        self.assertEqual(yesterday["date"], "2026-11-01")
        self.assertEqual((yesterday["temp_low"], yesterday["temp_high"]), (40.0, 70.0))
        self.assertTrue(yesterday["complete"])
        self.assertEqual(high, {"value": 80.0, "date": "2026-11-02", "ts": local_ts(2026, 11, 2, 0, 30)})

    def test_spring_forward_day_is_23_hours(self) -> None:
        # 8 Mar 2026 in Denver: 07:00 UTC 8 Mar to 06:00 UTC 9 Mar.
        self.add(local_ts(2026, 3, 8, 0, 30), air_temperature=10.0)
        self.add(local_ts(2026, 3, 8, 23, 30), air_temperature=30.0)
        self.add(local_ts(2026, 3, 9, 0, 30), air_temperature=50.0)

        yesterday = self.build(local_ts(2026, 3, 9, 12))["on_this_day"]["comparisons"][0]

        self.assertEqual(yesterday["date"], "2026-03-08")
        self.assertEqual((yesterday["temp_low"], yesterday["temp_high"], yesterday["readings"]), (10.0, 30.0, 2))

    def test_days_follow_the_station_timezone(self) -> None:
        # 20:00 in Denver on 5 Oct is 02:00 UTC on 6 Oct.
        self.add(local_ts(2026, 10, 5, 20), air_temperature=55.0)

        denver = self.build(local_ts(2026, 10, 6, 12), "America/Denver")
        utc = self.build(local_ts(2026, 10, 6, 12), "UTC")

        self.assertEqual(denver["records"]["all_time"]["temp_high"]["date"], "2026-10-05")
        self.assertEqual(utc["records"]["all_time"]["temp_high"]["date"], "2026-10-06")
        self.assertEqual(utc["timezone"], "UTC")


class TestMonthsBack(unittest.TestCase):
    def test_clamps_to_month_end(self) -> None:
        self.assertEqual(_months_back(date(2026, 3, 31), 1), date(2026, 2, 28))
        self.assertEqual(_months_back(date(2028, 2, 29), 12), date(2027, 2, 28))
        self.assertEqual(_months_back(date(2026, 1, 15), 1), date(2025, 12, 15))
        self.assertEqual(_months_back(date(2026, 11, 14), 12), date(2025, 11, 14))


if __name__ == "__main__":
    unittest.main()
