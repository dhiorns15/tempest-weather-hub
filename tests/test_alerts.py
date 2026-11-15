import os
import queue
import tempfile
import threading
import unittest

from weatherhub import alert_store
from weatherhub.alerts import (
    ALL_KINDS,
    AlertConfig,
    AlertEngine,
    build_senders,
    load_alert_config,
    run_alerts_forever,
    start_alerting,
)
from weatherhub.cache import LatestCache
from weatherhub.db import init_db, insert_observation
from weatherhub.notifier import EmailSender, WebhookSender

T0 = 1_800_000_000.0


class FakeClock:
    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class EngineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db_path)
        init_db(self.db_path)
        alert_store.init_alert_tables(self.db_path)

        self.cache = LatestCache()
        self.health = LatestCache()
        self.clock = FakeClock()
        self.sent = []

    def make_engine(self, kinds=ALL_KINDS, **overrides) -> AlertEngine:
        config = load_alert_config({}, overrides.pop("unit_system", "imperial"))
        config.kinds = frozenset(kinds)
        for key, value in overrides.items():
            setattr(config, key, value)
        return AlertEngine(
            config, self.cache, self.health, self.db_path, self.sent.append, clock=self.clock
        )

    def observe(self, **fields) -> None:
        """A fresh observation stamped with the fake clock's time."""
        self.cache.update({"time": int(self.clock.now), **fields})

    def kinds_sent(self) -> list[str]:
        return [alert.kind for alert in self.sent]


class TestConfig(unittest.TestCase):
    def test_imperial_defaults(self) -> None:
        config = load_alert_config({}, "imperial")
        self.assertEqual(config.kinds, frozenset(ALL_KINDS))
        self.assertEqual(config.no_data_minutes, 15)
        self.assertEqual(config.frost_temperature, 32)
        self.assertEqual(config.wind_gust, 40)
        self.assertEqual(config.lightning_distance, 10)
        self.assertEqual(config.units["temp_unit"], "°F")

    def test_metric_defaults(self) -> None:
        config = load_alert_config({}, "metric")
        self.assertEqual(config.frost_temperature, 0)
        self.assertEqual(config.wind_gust, 18)
        self.assertEqual(config.lightning_distance, 16)
        self.assertEqual(config.units["speed_unit"], "m/s")

    def test_overrides_and_kind_selection(self) -> None:
        config = load_alert_config(
            {"ALERT_KINDS": "no_data, Frost", "ALERT_FROST_TEMPERATURE": "35.5"}, "imperial"
        )
        self.assertEqual(config.kinds, frozenset({"no_data", "frost"}))
        self.assertEqual(config.frost_temperature, 35.5)

    def test_invalid_values_raise(self) -> None:
        with self.assertRaises(ValueError):
            load_alert_config({"ALERT_KINDS": "no_data,hail"}, "imperial")
        with self.assertRaises(ValueError):
            load_alert_config({"ALERT_WIND_GUST": "lots"}, "imperial")
        with self.assertRaises(ValueError):
            load_alert_config({"ALERT_NO_DATA_MINUTES": "0"}, "imperial")


class TestBuildSenders(unittest.TestCase):
    def test_nothing_configured(self) -> None:
        self.assertEqual(build_senders({}), [])

    def test_webhook_and_email(self) -> None:
        senders = build_senders(
            {
                "ALERT_WEBHOOK_URL": "https://ntfy.example/topic",
                "ALERT_WEBHOOK_FORMAT": "ntfy",
                "ALERT_SMTP_HOST": "mail.example",
                "ALERT_SMTP_SECURITY": "ssl",
                "ALERT_SMTP_USER": "me@example.com",
                "ALERT_EMAIL_TO": "a@example.com, b@example.com",
            }
        )
        webhook, email = senders
        self.assertIsInstance(webhook, WebhookSender)
        self.assertEqual(webhook.fmt, "ntfy")
        self.assertIsInstance(email, EmailSender)
        self.assertEqual(email.port, 465)  # default for implicit TLS
        self.assertEqual(email.sender, "me@example.com")  # falls back to the user
        self.assertEqual(email.recipients, ["a@example.com", "b@example.com"])

    def test_half_configured_destinations_raise(self) -> None:
        with self.assertRaises(ValueError):
            build_senders({"ALERT_SMTP_HOST": "mail.example"})  # no recipients
        with self.assertRaises(ValueError):
            build_senders({"ALERT_WEBHOOK_URL": "ftp://x"})
        with self.assertRaises(ValueError):
            build_senders({"ALERT_WEBHOOK_URL": "https://x", "ALERT_WEBHOOK_FORMAT": "xml"})


class TestNoData(EngineTestCase):
    def test_fires_after_threshold_then_recovers_once(self) -> None:
        engine = self.make_engine(kinds=["no_data"])
        self.observe(air_temperature=50)
        engine.tick()

        self.clock.advance(14 * 60)
        engine.tick()
        self.assertEqual(self.sent, [])

        self.clock.advance(61)
        engine.tick()
        self.assertEqual(self.kinds_sent(), ["no_data"])
        self.assertIn("No new observation", self.sent[0].message)

        # Still silent: no repeat.
        self.clock.advance(3600)
        engine.tick()
        self.assertEqual(self.kinds_sent(), ["no_data"])

        self.observe(air_temperature=51)
        engine.tick()
        self.assertEqual(self.kinds_sent(), ["no_data", "data_resumed"])
        self.assertEqual(self.sent[1].values["gap_minutes"], 75)

        engine.tick()
        self.assertEqual(len(self.sent), 2)

    def test_counts_from_startup_when_nothing_ever_arrived(self) -> None:
        engine = self.make_engine(kinds=["no_data"])
        self.clock.advance(15 * 60)
        engine.tick()
        self.assertEqual(self.kinds_sent(), ["no_data"])
        self.assertIn("No observation has been received since", self.sent[0].message)

    def test_old_history_does_not_fire_immediately_on_startup(self) -> None:
        insert_observation(self.db_path, {"time": int(T0 - 3 * 86400), "air_temperature": 1})
        engine = self.make_engine(kinds=["no_data"])
        engine.tick()
        self.assertEqual(self.sent, [])
        self.clock.advance(15 * 60)
        engine.tick()
        self.assertEqual(self.kinds_sent(), ["no_data"])
        self.assertEqual(self.sent[0].values["last_observation"], int(T0 - 3 * 86400))

    def test_stale_cloud_timestamp_counts_as_no_data(self) -> None:
        engine = self.make_engine(kinds=["no_data"])
        self.observe()
        stale_time = self.clock.now
        for _ in range(4):
            self.clock.advance(300)
            # The poller keeps succeeding but the station's own time doesn't move.
            self.cache.update({"time": int(stale_time), "rest_updated_at": int(stale_time)})
            engine.tick()
        self.assertEqual(self.kinds_sent(), ["no_data"])

    def test_restart_does_not_resend_and_still_sends_recovery(self) -> None:
        engine = self.make_engine(kinds=["no_data"])
        self.observe()
        engine.tick()
        self.clock.advance(20 * 60)
        engine.tick()
        self.assertEqual(self.kinds_sent(), ["no_data"])

        # "Restart": a new engine over the same database, empty cache.
        self.cache = LatestCache()
        self.clock.advance(60)
        restarted = self.make_engine(kinds=["no_data"])
        self.clock.advance(30 * 60)
        restarted.tick()
        self.assertEqual(self.kinds_sent(), ["no_data"])

        self.observe()
        restarted.tick()
        self.assertEqual(self.kinds_sent(), ["no_data", "data_resumed"])

    def test_not_subject_to_cooldown(self) -> None:
        engine = self.make_engine(kinds=["no_data"], cooldown_minutes=600)
        self.observe()
        for _ in range(2):
            self.clock.advance(16 * 60)
            engine.tick()
            self.observe()
            engine.tick()
        self.assertEqual(self.kinds_sent(), ["no_data", "data_resumed"] * 2)


class TestFrostAndWind(EngineTestCase):
    def test_frost_fires_once_with_hysteresis(self) -> None:
        engine = self.make_engine(kinds=["frost"], cooldown_minutes=0)
        for temp in (34.0, 32.0, 31.0, 32.5, 33.9, 31.5):
            self.observe(air_temperature=temp)
            engine.tick()
            self.clock.advance(60)
        # 32.5 and 33.9 are inside the 2 F margin, so 31.5 does not re-fire.
        self.assertEqual(self.kinds_sent(), ["frost"])
        self.assertIn("32°F", self.sent[0].message)

        self.observe(air_temperature=34.5)  # clears the margin -> re-armed
        engine.tick()
        self.clock.advance(60)
        self.observe(air_temperature=31.0)
        engine.tick()
        self.assertEqual(self.kinds_sent(), ["frost", "frost"])

    def test_cooldown_suppresses_a_quick_second_episode(self) -> None:
        engine = self.make_engine(kinds=["frost"], cooldown_minutes=60)
        self.observe(air_temperature=30)
        engine.tick()
        self.clock.advance(20 * 60)
        self.observe(air_temperature=40)
        engine.tick()
        self.clock.advance(10 * 60)
        self.observe(air_temperature=30)
        engine.tick()  # within cooldown: active but silent
        self.assertEqual(self.kinds_sent(), ["frost"])

        self.clock.advance(3 * 3600)
        self.observe(air_temperature=29)
        engine.tick()  # same (silent) episode continues - still nothing
        self.assertEqual(self.kinds_sent(), ["frost"])

        self.observe(air_temperature=40)
        engine.tick()
        self.clock.advance(60)
        self.observe(air_temperature=30)
        engine.tick()
        self.assertEqual(self.kinds_sent(), ["frost", "frost"])

    def test_stale_values_are_ignored(self) -> None:
        engine = self.make_engine(kinds=["frost"])
        self.cache.set({"time": int(T0 - 3600), "air_temperature": 20})
        engine.tick()
        self.assertEqual(self.sent, [])

    def test_wind_gust_threshold_metric(self) -> None:
        engine = self.make_engine(kinds=["wind"], unit_system="metric", cooldown_minutes=0)
        for gust in (10.0, 18.0, 15.0, 19.0, 13.0, 20.0):
            self.observe(wind_gust=gust, wind_avg=8.0)
            engine.tick()
        # Re-arms only below 18 - 4.5 = 13.5.
        self.assertEqual(self.kinds_sent(), ["wind", "wind"])
        self.assertIn("18 m/s", self.sent[0].message)
        self.assertEqual(self.sent[0].values["wind_gust"], 18.0)

    def test_restart_while_frost_ongoing_does_not_resend(self) -> None:
        engine = self.make_engine(kinds=["frost"])
        self.observe(air_temperature=28)
        engine.tick()
        restarted = self.make_engine(kinds=["frost"])
        self.observe(air_temperature=27)
        restarted.tick()
        self.assertEqual(self.kinds_sent(), ["frost"])


class TestLightningAndRain(EngineTestCase):
    def strike(self, ts: float, km: float) -> dict:
        return {"type": "evt_strike", "hub_sn": "HB-1", "evt": [int(ts), km, 1000]}

    def test_udp_strike_within_distance_fires_once_per_storm(self) -> None:
        engine = self.make_engine(kinds=["lightning"])
        engine.handle_event(self.strike(self.clock.now, 30))  # 18.6 mi: too far
        self.assertEqual(self.sent, [])

        engine.handle_event(self.strike(self.clock.now, 8))  # ~5 mi
        self.assertEqual(self.kinds_sent(), ["lightning"])
        self.assertEqual(self.sent[0].values["distance"], 5.0)
        self.assertEqual(self.sent[0].values["unit"], "mi")

        for _ in range(5):
            self.clock.advance(10 * 60)
            engine.handle_event(self.strike(self.clock.now, 5))
            engine.tick()
        self.assertEqual(len(self.sent), 1)

        # 31 quiet minutes ends the storm; after the cooldown a new one fires.
        self.clock.advance(31 * 60)
        engine.tick()
        self.assertFalse(engine.states["lightning"]["active"])
        self.clock.advance(3600)
        engine.handle_event(self.strike(self.clock.now, 5))
        self.assertEqual(self.kinds_sent(), ["lightning", "lightning"])

    def test_cloud_last_strike_fires_only_when_recent(self) -> None:
        engine = self.make_engine(kinds=["lightning"])
        self.observe(
            lightning_strike_last_epoch=int(T0 - 86400), lightning_strike_last_distance=3
        )
        engine.tick()
        self.assertEqual(self.sent, [])

        self.observe(
            lightning_strike_last_epoch=int(T0 - 120), lightning_strike_last_distance=7
        )
        engine.tick()
        engine.tick()
        self.assertEqual(self.kinds_sent(), ["lightning"])
        self.assertEqual(self.sent[0].values["source"], "Tempest cloud")

    def test_udp_rain_start_and_clear(self) -> None:
        engine = self.make_engine(kinds=["rain"])
        engine.handle_event({"type": "evt_precip", "hub_sn": "HB-1", "evt": [int(T0)]})
        self.assertEqual(self.kinds_sent(), ["rain"])

        # Ongoing rain reported by obs_st (index 12 = mm over the last minute).
        obs = [0] * 18
        for _ in range(6):
            self.clock.advance(15 * 60)
            obs[0] = int(self.clock.now)
            obs[12] = 0.2
            engine.handle_event({"type": "obs_st", "obs": [list(obs)]})
            engine.tick()
        self.assertTrue(engine.states["rain"]["active"])
        self.assertEqual(len(self.sent), 1)

        self.clock.advance(61 * 60)
        engine.tick()
        self.assertFalse(engine.states["rain"]["active"])

    def test_cloud_rain_from_increasing_daily_total(self) -> None:
        engine = self.make_engine(kinds=["rain"])
        self.observe(precip_accum_local_day=0.0, precip_minutes_local_day=0)
        engine.tick()
        self.clock.advance(300)
        self.observe(precip_accum_local_day=0.0, precip_minutes_local_day=0)
        engine.tick()
        self.assertEqual(self.sent, [])
        self.clock.advance(300)
        self.observe(precip_accum_local_day=0.02, precip_minutes_local_day=3)
        engine.tick()
        self.assertEqual(self.kinds_sent(), ["rain"])
        self.assertEqual(self.sent[0].values["source"], "Tempest cloud")

    def test_cloud_rain_ignores_midnight_reset_and_first_reading(self) -> None:
        engine = self.make_engine(kinds=["rain"])
        self.observe(precip_accum_local_day=0.5, precip_minutes_local_day=40)
        engine.tick()  # first reading is only a baseline
        self.clock.advance(300)
        self.observe(precip_accum_local_day=0.0, precip_minutes_local_day=0)
        engine.tick()
        self.assertEqual(self.sent, [])

    def test_disabled_kind_ignores_events(self) -> None:
        engine = self.make_engine(kinds=["no_data"])
        engine.handle_event(self.strike(self.clock.now, 1))
        engine.handle_event({"type": "evt_precip", "evt": [int(T0)]})
        self.assertEqual(self.sent, [])

    def test_malformed_events_are_ignored(self) -> None:
        engine = self.make_engine()
        for message in (
            {"type": "evt_strike"},
            {"type": "evt_strike", "evt": ["x", None]},
            {"type": "obs_st", "obs": "nope"},
            {"type": "obs_st", "obs": [[1, 2]]},
            "not a dict",
        ):
            engine.handle_event(message)  # type: ignore[arg-type]
        self.assertEqual(self.sent, [])


class TestBattery(EngineTestCase):
    def test_low_battery_with_margin(self) -> None:
        engine = self.make_engine(kinds=["battery"], cooldown_minutes=0)
        for volts in (2.6, 2.40, 2.38, 2.44, 2.39, 2.46, 2.35):
            self.health.update({"device_voltage": volts})
            engine.tick()
        self.assertEqual(self.kinds_sent(), ["battery", "battery"])


class TestAlertLog(EngineTestCase):
    def test_alerts_are_logged_with_ids(self) -> None:
        engine = self.make_engine(kinds=["frost"])
        self.observe(air_temperature=20, location_name="Tower 1")
        engine.tick()

        self.assertIsNotNone(self.sent[0].id)
        self.assertEqual(self.sent[0].station, "Tower 1")
        logged = alert_store.recent_alerts(self.db_path)
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0]["kind"], "frost")
        self.assertEqual(logged[0]["status"], "pending")

        alert_store.update_alert_status(self.db_path, logged[0]["id"], "sent", "webhook: sent")
        self.assertEqual(alert_store.recent_alerts(self.db_path)[0]["status"], "sent")

    def test_station_name_override(self) -> None:
        engine = self.make_engine(kinds=["frost"], station_name="Back field")
        self.observe(air_temperature=20, location_name="Tower 1")
        engine.tick()
        self.assertEqual(self.sent[0].station, "Back field")
        self.assertIn("Back field", self.sent[0].title)

    def test_recent_alerts_without_tables(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, path)
        self.assertEqual(alert_store.recent_alerts(path), [])


class TestRobustness(EngineTestCase):
    def test_a_failing_check_does_not_stop_the_others(self) -> None:
        engine = self.make_engine(kinds=["frost", "battery"])
        engine._check_frost = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
        self.health.update({"device_voltage": 2.0})
        with self.assertLogs("weatherhub", level="ERROR"):
            engine.tick()
        self.assertEqual(self.kinds_sent(), ["battery"])

    def test_run_loop_processes_events_and_survives_errors(self) -> None:
        engine = self.make_engine(kinds=["lightning"])
        events: queue.Queue = queue.Queue()
        stop = threading.Event()
        calls = []
        original_tick = engine.tick

        def flaky_tick() -> None:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("first tick fails")
            original_tick()

        engine.tick = flaky_tick  # type: ignore[method-assign]
        thread = threading.Thread(
            target=run_alerts_forever, args=(engine, events, stop, 0.05), daemon=True
        )
        with self.assertLogs("weatherhub", level="ERROR"):
            thread.start()
            events.put({"type": "evt_strike", "evt": [int(self.clock.now), 3, 10]})
            for _ in range(100):
                if self.sent and len(calls) > 1:
                    break
                threading.Event().wait(0.02)
            stop.set()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.kinds_sent(), ["lightning"])
        self.assertGreater(len(calls), 1)


class TestStartAlerting(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db_path)
        init_db(self.db_path)

    def test_off_without_destination(self) -> None:
        result = start_alerting(
            LatestCache(), LatestCache(), self.db_path, "imperial", threading.Event(), env={}
        )
        self.assertIsNone(result)

    def test_bad_config_disables_instead_of_raising(self) -> None:
        with self.assertLogs("weatherhub", level="ERROR"):
            result = start_alerting(
                LatestCache(),
                LatestCache(),
                self.db_path,
                "imperial",
                threading.Event(),
                env={"ALERT_WEBHOOK_URL": "https://x.example/hook", "ALERT_WIND_GUST": "fast"},
            )
        self.assertIsNone(result)

    def test_starts_threads_when_configured(self) -> None:
        stop = threading.Event()
        service = start_alerting(
            LatestCache(),
            LatestCache(),
            self.db_path,
            "imperial",
            stop,
            env={"ALERT_WEBHOOK_URL": "http://127.0.0.1:9/hook"},
        )
        self.assertIsNotNone(service)
        assert service is not None
        self.assertTrue(service.thread.is_alive())
        stop.set()
        service.notifier.stop()
        service.thread.join(timeout=5)
        service.notifier.join(timeout=5)
        self.assertFalse(service.thread.is_alive())


if __name__ == "__main__":
    unittest.main()
