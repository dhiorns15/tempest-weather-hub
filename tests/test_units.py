import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from weatherhub.cache import LatestCache
from weatherhub.db import init_db
from weatherhub.server import make_server
from weatherhub.tempest_client import _UNITS_BY_SYSTEM, display_units

IMPERIAL = {
    "system": "imperial",
    "temp": "°F",
    "wind": "mph",
    "pressure": "inHg",
    "precip": "in",
    "distance": "mi",
}
METRIC = {
    "system": "metric",
    "temp": "°C",
    "wind": "m/s",
    "pressure": "mb",
    "precip": "mm",
    "distance": "km",
}


class TestDisplayUnits(unittest.TestCase):
    def test_imperial(self) -> None:
        self.assertEqual(display_units("imperial"), IMPERIAL)

    def test_metric(self) -> None:
        self.assertEqual(display_units("metric"), METRIC)

    def test_unrecognised_value_is_imperial(self) -> None:
        # Same fallback fetch_tempest and the UDP listener use, so the labels
        # always describe the numbers actually stored.
        for value in ("", "si", "Metric", "METRIC", "uk"):
            with self.subTest(value=value):
                self.assertEqual(display_units(value), IMPERIAL)

    def test_every_requested_system_has_labels(self) -> None:
        for system in _UNITS_BY_SYSTEM:
            with self.subTest(system=system):
                self.assertEqual(display_units(system)["system"], system)

    def test_returns_a_fresh_dict(self) -> None:
        display_units("metric")["temp"] = "K"
        self.assertEqual(display_units("metric")["temp"], "°C")


class TestCapabilitiesUnits(unittest.TestCase):
    def _make_server(self, **kwargs):
        static_dir = Path(tempfile.mkdtemp())
        (static_dir / "index.html").write_text("<h1>home</h1>", encoding="utf-8")
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, db_path)
        init_db(db_path)

        cache = LatestCache()
        server = make_server(
            cache,
            LatestCache(),
            LatestCache(),
            db_path,
            static_dir,
            port=0,
            cloud_configured=True,
            **kwargs,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, timeout=5)
        return server.server_address[1], cache

    def _get(self, port: int, path: str) -> tuple[int, bytes]:
        url = f"http://127.0.0.1:{port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_defaults_to_imperial(self) -> None:
        port, _ = self._make_server()

        status, body = self._get(port, "/api/capabilities")

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["units"], IMPERIAL)

    def test_metric(self) -> None:
        port, _ = self._make_server(unit_system="metric")

        _, body = self._get(port, "/api/capabilities")

        self.assertEqual(json.loads(body)["units"], METRIC)

    def test_unrecognised_unit_system_reports_imperial(self) -> None:
        port, _ = self._make_server(unit_system="kelvin")

        _, body = self._get(port, "/api/capabilities")

        self.assertEqual(json.loads(body)["units"], IMPERIAL)

    def test_capabilities_keeps_source_flags(self) -> None:
        port, _ = self._make_server(udp_configured=True, unit_system="metric")

        _, body = self._get(port, "/api/capabilities")

        payload = json.loads(body)
        self.assertEqual(payload["cloud_configured"], True)
        self.assertEqual(payload["udp_configured"], True)

    def test_widget_labels_temperature_in_configured_units(self) -> None:
        port, cache = self._make_server(unit_system="metric")
        cache.set({"air_temperature": 12.4, "wind_avg": 2.44, "wind_direction": 90})

        _, body = self._get(port, "/widget/current")

        text = body.decode("utf-8")
        self.assertIn("°C", text)
        self.assertNotIn("°F", text)
        self.assertIn("Wind 2.4 m/s E", text)

    def test_widget_accepts_auto_theme(self) -> None:
        port, _ = self._make_server()

        status, body = self._get(port, "/widget/current?theme=auto")

        self.assertEqual(status, 200)
        self.assertIn("prefers-color-scheme: dark", body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
