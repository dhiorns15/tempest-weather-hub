import json
import os
import socket
import tempfile
import threading
import time
import unittest

from weatherhub.cache import LatestCache
from weatherhub.db import init_db, query_history
from weatherhub.udp_listener import (
    listen_forever,
    parse_device_status,
    parse_hub_status,
    parse_obs_st,
)

# Real samples captured from a live Tempest hub broadcast.
_SAMPLE_OBS_ST = {
    "serial_number": "ST-00113143",
    "type": "obs_st",
    "hub_sn": "HB-00118618",
    "obs": [
        [
            1785177022, 0.37, 0.65, 0.91, 357, 15, 851.22, 30.36, 39.16,
            58662, 5.2, 489, 0.0, 0, 0, 0, 2.647, 1,
        ]
    ],
    "firmware_revision": 181,
}

_SAMPLE_DEVICE_STATUS = {
    "serial_number": "ST-00113143",
    "type": "device_status",
    "hub_sn": "HB-00118618",
    "timestamp": 1785177022,
    "uptime": 60352563,
    "voltage": 2.647,
    "firmware_revision": 181,
    "rssi": -55,
    "hub_rssi": -53,
    "sensor_status": 666111,
    "debug": 0,
}

_SAMPLE_HUB_STATUS = {
    "serial_number": "HB-00118618",
    "type": "hub_status",
    "firmware_revision": "194",
    "uptime": 11132894,
    "rssi": -70,
    "timestamp": 1785177008,
    "reset_flags": "PIN,SFT,HRDFLT",
    "seq": 1111280,
    "radio_stats": [26, 1, 0, 3, 35065],
    "mqtt_stats": [94, 0],
}


class TestParseObsSt(unittest.TestCase):
    def test_converts_to_imperial_by_default(self) -> None:
        parsed = parse_obs_st(_SAMPLE_OBS_ST)

        assert parsed is not None
        self.assertAlmostEqual(parsed["air_temperature"], round(30.36 * 9 / 5 + 32, 1), places=5)
        self.assertAlmostEqual(parsed["wind_lull"], round(0.37 * 2.236936, 1), places=5)
        self.assertAlmostEqual(parsed["wind_avg"], round(0.65 * 2.236936, 1), places=5)
        self.assertAlmostEqual(parsed["wind_gust"], round(0.91 * 2.236936, 1), places=5)
        self.assertAlmostEqual(parsed["station_pressure"], round(851.22 * 0.02953, 2), places=5)
        self.assertEqual(parsed["relative_humidity"], 39.16)
        self.assertEqual(parsed["wind_direction"], 357)
        self.assertEqual(parsed["uv"], 5.2)
        self.assertEqual(parsed["solar_radiation"], 489)
        self.assertEqual(parsed["time"], 1785177022)
        self.assertEqual(parsed["udp_updated_at"], 1785177022)

    def test_metric_passthrough(self) -> None:
        parsed = parse_obs_st(_SAMPLE_OBS_ST, unit_system="metric")

        assert parsed is not None
        self.assertEqual(parsed["air_temperature"], 30.36)
        self.assertEqual(parsed["wind_lull"], 0.37)
        self.assertEqual(parsed["wind_avg"], 0.65)
        self.assertEqual(parsed["wind_gust"], 0.91)
        self.assertEqual(parsed["station_pressure"], 851.22)

    def test_non_obs_st_shaped_message_returns_none(self) -> None:
        self.assertIsNone(parse_obs_st({"type": "hub_status"}))
        self.assertIsNone(parse_obs_st({"type": "obs_st", "obs": []}))
        self.assertIsNone(parse_obs_st({"type": "obs_st", "obs": [[1, 2, 3]]}))
        self.assertIsNone(parse_obs_st({"type": "obs_st"}))


class TestParseStatusMessages(unittest.TestCase):
    def test_parse_device_status(self) -> None:
        parsed = parse_device_status(_SAMPLE_DEVICE_STATUS)

        self.assertEqual(parsed["device_voltage"], 2.647)
        self.assertEqual(parsed["device_rssi"], -55)
        self.assertEqual(parsed["device_hub_rssi"], -53)
        self.assertEqual(parsed["device_firmware_revision"], 181)
        self.assertEqual(parsed["device_uptime"], 60352563)

    def test_parse_hub_status(self) -> None:
        parsed = parse_hub_status(_SAMPLE_HUB_STATUS)

        self.assertEqual(parsed["hub_uptime"], 11132894)
        self.assertEqual(parsed["hub_rssi"], -70)
        self.assertEqual(parsed["hub_firmware_revision"], "194")
        self.assertEqual(parsed["hub_reset_flags"], "PIN,SFT,HRDFLT")


class TestListenForever(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db_path)
        init_db(self.db_path)

    def _send(self, port: int, message: dict) -> None:
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.sendto(json.dumps(message).encode("utf-8"), ("127.0.0.1", port))
        client.close()

    def test_updates_cache_from_obs_st_broadcast(self) -> None:
        cache = LatestCache()
        health_cache = LatestCache()
        stop_event = threading.Event()
        port = 52099

        thread = threading.Thread(
            target=listen_forever,
            args=(cache, health_cache, self.db_path, port, "", "imperial", stop_event),
            daemon=True,
        )
        thread.start()
        time.sleep(0.3)  # let the socket bind before we send

        try:
            self._send(port, _SAMPLE_OBS_ST)
            time.sleep(0.3)
        finally:
            stop_event.set()
            thread.join(timeout=5)

        snapshot = cache.get()
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot["time"], 1785177022)

    def test_obs_st_broadcast_is_also_written_to_history(self) -> None:
        cache = LatestCache()
        health_cache = LatestCache()
        stop_event = threading.Event()
        port = 52102

        thread = threading.Thread(
            target=listen_forever,
            args=(cache, health_cache, self.db_path, port, "", "imperial", stop_event),
            daemon=True,
        )
        thread.start()
        time.sleep(0.3)

        try:
            self._send(port, _SAMPLE_OBS_ST)
            time.sleep(0.3)
        finally:
            stop_event.set()
            thread.join(timeout=5)

        rows = query_history(self.db_path, 0, 2_000_000_000)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ts"], 1785177022)
        self.assertIsNotNone(rows[0]["air_temperature"])

    def test_updates_health_cache_from_status_broadcasts(self) -> None:
        cache = LatestCache()
        health_cache = LatestCache()
        stop_event = threading.Event()
        port = 52101

        thread = threading.Thread(
            target=listen_forever,
            args=(cache, health_cache, self.db_path, port, "", "imperial", stop_event),
            daemon=True,
        )
        thread.start()
        time.sleep(0.3)

        try:
            self._send(port, _SAMPLE_DEVICE_STATUS)
            self._send(port, _SAMPLE_HUB_STATUS)
            time.sleep(0.3)
        finally:
            stop_event.set()
            thread.join(timeout=5)

        snapshot = health_cache.get()
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        # Merged from both messages, not clobbered by one another.
        self.assertEqual(snapshot["device_voltage"], 2.647)
        self.assertEqual(snapshot["hub_uptime"], 11132894)

    def test_hub_serial_filter_rejects_other_hubs(self) -> None:
        cache = LatestCache()
        health_cache = LatestCache()
        stop_event = threading.Event()
        port = 52100

        thread = threading.Thread(
            target=listen_forever,
            args=(cache, health_cache, self.db_path, port, "HB-99999999", "imperial", stop_event),
            daemon=True,
        )
        thread.start()
        time.sleep(0.3)

        try:
            # hub_sn in the sample is HB-00118618, which doesn't match the filter
            self._send(port, _SAMPLE_OBS_ST)
            time.sleep(0.3)
        finally:
            stop_event.set()
            thread.join(timeout=5)

        self.assertIsNone(cache.get())


if __name__ == "__main__":
    unittest.main()
