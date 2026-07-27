import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from weatherhub.api_keys import create_key, revoke_key, list_keys
from weatherhub.cache import LatestCache
from weatherhub.db import init_db, insert_observation
from weatherhub.server import make_server


class TestServer(unittest.TestCase):
    def setUp(self) -> None:
        self.static_dir = Path(tempfile.mkdtemp())
        (self.static_dir / "index.html").write_text("<h1>home</h1>", encoding="utf-8")
        sub = self.static_dir / "css"
        sub.mkdir()
        (sub / "site.css").write_text("body{color:red}", encoding="utf-8")

        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db_path)
        init_db(self.db_path)

        self.cache = LatestCache()
        self.forecast_cache = LatestCache()
        self.station_health_cache = LatestCache()
        self.server = make_server(
            self.cache,
            self.forecast_cache,
            self.station_health_cache,
            self.db_path,
            self.static_dir,
            port=0,
            cloud_configured=True,
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _get(self, path: str) -> tuple[int, bytes]:
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_healthz(self) -> None:
        status, body = self._get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "ok"})

    def test_api_current_returns_503_before_first_poll(self) -> None:
        status, body = self._get("/api/current")
        self.assertEqual(status, 503)
        self.assertIn("error", json.loads(body))

    def test_api_current_returns_cached_snapshot(self) -> None:
        self.cache.set({"air_temperature": 21.5})

        status, body = self._get("/api/current")

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"air_temperature": 21.5})

    def test_api_forecast_returns_503_before_first_poll(self) -> None:
        status, body = self._get("/api/forecast")
        self.assertEqual(status, 503)
        self.assertIn("error", json.loads(body))

    def test_api_forecast_returns_cached_snapshot(self) -> None:
        self.forecast_cache.set({"hourly": [{"time": 1}], "daily": []})

        status, body = self._get("/api/forecast")

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"hourly": [{"time": 1}], "daily": []})

    def test_station_health_returns_503_before_udp_data(self) -> None:
        status, body = self._get("/api/station-health")
        self.assertEqual(status, 503)
        self.assertIn("error", json.loads(body))

    def test_station_health_returns_cached_snapshot(self) -> None:
        self.station_health_cache.set({"device_voltage": 2.6, "hub_uptime": 12345})

        status, body = self._get("/api/station-health")

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"device_voltage": 2.6, "hub_uptime": 12345})

    def test_kiosk_weather_returns_503_before_first_poll(self) -> None:
        status, body = self._get("/kiosk/weather")
        self.assertEqual(status, 503)
        self.assertIn("error", json.loads(body))

    def test_kiosk_weather_returns_owm_shaped_response(self) -> None:
        self.cache.set(
            {
                "location_name": "Tower 1",
                "icon": "clear-day",
                "conditions": "Clear",
                "air_temperature": 21.5,
                "relative_humidity": 55,
                "wind_avg": 3.2,
                "wind_direction": 180,
                "time": 1_700_000_000,
            }
        )
        self.forecast_cache.set({"hourly": [], "daily": []})

        status, body = self._get("/kiosk/weather")
        parsed = json.loads(body)

        self.assertEqual(status, 200)
        self.assertEqual(parsed["name"], "Tower 1")
        self.assertEqual(parsed["weather"], [{"id": 800, "description": "Clear"}])
        self.assertEqual(parsed["main"]["temp"], 21.5)
        self.assertEqual(parsed["list"], [])

    def test_kiosk_weather_works_without_forecast_cached_yet(self) -> None:
        self.cache.set({"location_name": "Tower 1", "air_temperature": 21.5})

        status, body = self._get("/kiosk/weather")

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["list"], [])

    def test_root_serves_index_html(self) -> None:
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"<h1>home</h1>")

    def test_serves_nested_static_file(self) -> None:
        status, body = self._get("/css/site.css")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"body{color:red}")

    def test_unknown_path_returns_404(self) -> None:
        status, body = self._get("/nope.html")
        self.assertEqual(status, 404)
        self.assertIn("error", json.loads(body))

    def test_path_traversal_is_blocked(self) -> None:
        status, _ = self._get("/../outside.txt")
        self.assertEqual(status, 404)

    def test_widget_current_renders_html(self) -> None:
        self.cache.set({"air_temperature": 21.5, "conditions": "Clear", "icon": "clear-day"})

        url = f"http://127.0.0.1:{self.port}/widget/current"
        with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Content-Type"], "text/html; charset=utf-8")
            body = response.read().decode("utf-8")

        self.assertIn("22", body)
        self.assertIn("Clear", body)

    def test_widget_current_dark_theme(self) -> None:
        url = f"http://127.0.0.1:{self.port}/widget/current?theme=dark"
        with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
            body = response.read().decode("utf-8")

        self.assertIn("#1d2025", body)


class TestServerHistory(unittest.TestCase):
    def setUp(self) -> None:
        self.static_dir = Path(tempfile.mkdtemp())
        (self.static_dir / "index.html").write_text("<h1>home</h1>", encoding="utf-8")

        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db_path)
        init_db(self.db_path)

        self.now = int(time.time())
        for i in range(5):
            insert_observation(
                self.db_path,
                {"time": self.now - i * 3600, "air_temperature": 10 + i},
            )

        self.server = make_server(LatestCache(), LatestCache(), LatestCache(), self.db_path, self.static_dir, port=0, cloud_configured=True)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _get(self, path: str) -> tuple[int, dict]:
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_raw_history_defaults_to_last_24h(self) -> None:
        status, body = self._get("/site/history")

        self.assertEqual(status, 200)
        self.assertEqual(body["resolution"], "raw")
        self.assertEqual(len(body["observations"]), 5)

    def test_explicit_start_end_narrows_range(self) -> None:
        start = self.now - 3600 * 2
        end = self.now

        status, body = self._get(f"/site/history?start={start}&end={end}")

        self.assertEqual(status, 200)
        self.assertEqual(len(body["observations"]), 3)

    def test_hourly_resolution(self) -> None:
        status, body = self._get(
            f"/site/history?start={self.now - 3600 * 5}&end={self.now}&resolution=hourly"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["resolution"], "hourly")
        self.assertGreater(len(body["observations"]), 0)

    def test_invalid_resolution_returns_400(self) -> None:
        status, body = self._get("/site/history?resolution=weekly")
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_non_integer_timestamp_returns_400(self) -> None:
        status, body = self._get("/site/history?start=not-a-number")
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_start_after_end_returns_400(self) -> None:
        status, body = self._get(f"/site/history?start={self.now}&end={self.now - 100}")
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_raw_resolution_over_31_days_returns_400(self) -> None:
        start = self.now - 40 * 86400
        status, body = self._get(f"/site/history?start={start}&end={self.now}")
        self.assertEqual(status, 400)
        self.assertIn("error", body)


class TestServerHistoryAuth(unittest.TestCase):
    def setUp(self) -> None:
        self.static_dir = Path(tempfile.mkdtemp())
        (self.static_dir / "index.html").write_text("<h1>home</h1>", encoding="utf-8")

        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db_path)
        init_db(self.db_path)
        insert_observation(self.db_path, {"time": int(time.time()), "air_temperature": 20})

        self.server = make_server(LatestCache(), LatestCache(), LatestCache(), self.db_path, self.static_dir, port=0, cloud_configured=True)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _get(self, path: str, token: str | None = None) -> tuple[int, dict]:
        url = f"http://127.0.0.1:{self.port}{path}"
        request = urllib.request.Request(url)
        if token is not None:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_no_key_returns_401(self) -> None:
        status, body = self._get("/api/history")
        self.assertEqual(status, 401)
        self.assertIn("error", body)

    def test_bogus_key_returns_401(self) -> None:
        status, body = self._get("/api/history", token="wh_not-a-real-key")
        self.assertEqual(status, 401)

    def test_valid_key_returns_200(self) -> None:
        key = create_key(self.db_path, "test-app")

        status, body = self._get("/api/history", token=key)

        self.assertEqual(status, 200)
        self.assertEqual(len(body["observations"]), 1)

    def test_revoked_key_returns_401(self) -> None:
        key = create_key(self.db_path, "test-app")
        [row] = list_keys(self.db_path)
        revoke_key(self.db_path, row["id"])

        status, _ = self._get("/api/history", token=key)

        self.assertEqual(status, 401)

    def test_site_history_route_needs_no_key(self) -> None:
        status, body = self._get("/site/history")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["observations"]), 1)


class TestServerCapabilities(unittest.TestCase):
    def _make_server(self, *, cloud_configured: bool, udp_configured: bool = False):
        static_dir = Path(tempfile.mkdtemp())
        (static_dir / "index.html").write_text("<h1>home</h1>", encoding="utf-8")
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, db_path)
        init_db(db_path)

        server = make_server(
            LatestCache(),
            LatestCache(),
            LatestCache(),
            db_path,
            static_dir,
            port=0,
            cloud_configured=cloud_configured,
            udp_configured=udp_configured,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, timeout=5)
        return server.server_address[1]

    def _get(self, port: int, path: str) -> tuple[int, dict]:
        url = f"http://127.0.0.1:{port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_capabilities_reflects_both_configured(self) -> None:
        port = self._make_server(cloud_configured=True, udp_configured=True)

        status, body = self._get(port, "/api/capabilities")

        self.assertEqual(status, 200)
        self.assertEqual(body, {"cloud_configured": True, "udp_configured": True})

    def test_capabilities_reflects_udp_only(self) -> None:
        port = self._make_server(cloud_configured=False, udp_configured=True)

        status, body = self._get(port, "/api/capabilities")

        self.assertEqual(status, 200)
        self.assertEqual(body, {"cloud_configured": False, "udp_configured": True})

    def test_kiosk_weather_disabled_when_cloud_not_configured(self) -> None:
        port = self._make_server(cloud_configured=False, udp_configured=True)

        status, body = self._get(port, "/kiosk/weather")

        self.assertEqual(status, 503)
        self.assertIn("error", body)

    def test_forecast_disabled_when_cloud_not_configured(self) -> None:
        port = self._make_server(cloud_configured=False, udp_configured=True)

        status, body = self._get(port, "/api/forecast")

        self.assertEqual(status, 503)
        self.assertIn("error", body)

    def test_api_current_still_works_when_cloud_not_configured(self) -> None:
        # /api/current is source-agnostic - a UDP-only setup still populates it.
        port = self._make_server(cloud_configured=False, udp_configured=True)

        status, body = self._get(port, "/api/current")

        self.assertEqual(status, 503)  # no data yet, but not permanently disabled
        self.assertIn("not yet available", body["error"])


if __name__ == "__main__":
    unittest.main()
