"""The small hooks alerting adds to existing modules: GET /api/alerts and
the UDP listener forwarding events to the alert evaluator's queue."""

import json
import os
import queue
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from weatherhub import alert_store
from weatherhub.cache import LatestCache
from weatherhub.db import init_db
from weatherhub.server import make_server
from weatherhub.udp_listener import listen_forever


class TestAlertsEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db_path)
        init_db(self.db_path)
        self.static_dir = Path(tempfile.mkdtemp())

    def start(self, **kwargs) -> int:
        server = make_server(
            LatestCache(),
            LatestCache(),
            LatestCache(),
            self.db_path,
            self.static_dir,
            port=0,
            cloud_configured=True,
            **kwargs,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop() -> None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.addCleanup(stop)
        return server.server_address[1]

    def get(self, port: int, path: str) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_disabled_and_no_tables(self) -> None:
        port = self.start()
        self.assertEqual(self.get(port, "/api/alerts"), (200, {"enabled": False, "alerts": []}))

    def test_lists_most_recent_first_with_limit(self) -> None:
        alert_store.init_alert_tables(self.db_path)
        for i in range(3):
            alert_store.log_alert(
                self.db_path, 1000 + i, "frost", f"t{i}", f"m{i}", "Tower 1", {"n": i}, "sent"
            )
        port = self.start(alerts_enabled=True)

        status, body = self.get(port, "/api/alerts?limit=2")
        self.assertEqual(status, 200)
        self.assertTrue(body["enabled"])
        self.assertEqual([a["title"] for a in body["alerts"]], ["t2", "t1"])
        self.assertEqual(
            body["alerts"][0],
            {
                "id": 3,
                "time": 1002,
                "kind": "frost",
                "title": "t2",
                "message": "m2",
                "station": "Tower 1",
                "values": {"n": 2},
                "status": "sent",
            },
        )
        self.assertNotIn("detail", body["alerts"][0])

    def test_bad_limit(self) -> None:
        port = self.start()
        status, body = self.get(port, "/api/alerts?limit=abc")
        self.assertEqual(status, 400)


class TestUdpForwarding(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db_path)
        init_db(self.db_path)

    def run_listener(self, port: int, hub_serial: str, messages: list[dict]) -> queue.Queue:
        events: queue.Queue = queue.Queue()
        stop = threading.Event()
        thread = threading.Thread(
            target=listen_forever,
            args=(LatestCache(), LatestCache(), self.db_path, port, hub_serial, "imperial", stop),
            kwargs={"event_queue": events},
            daemon=True,
        )
        thread.start()
        time.sleep(0.3)
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            for message in messages:
                client.sendto(json.dumps(message).encode("utf-8"), ("127.0.0.1", port))
            client.close()
            time.sleep(0.3)
        finally:
            stop.set()
            thread.join(timeout=5)
        return events

    def drain(self, events: queue.Queue) -> list[str]:
        types = []
        while not events.empty():
            types.append(events.get_nowait()["type"])
        return types

    def test_forwards_event_messages_only(self) -> None:
        events = self.run_listener(
            52110,
            "",
            [
                {"type": "evt_strike", "hub_sn": "HB-1", "evt": [1, 5, 100]},
                {"type": "evt_precip", "hub_sn": "HB-1", "evt": [1]},
                {"type": "rapid_wind", "hub_sn": "HB-1", "ob": [1, 1.0, 90]},
                {"type": "hub_status", "serial_number": "HB-1"},
            ],
        )
        self.assertEqual(self.drain(events), ["evt_strike", "evt_precip"])

    def test_respects_hub_serial_filter(self) -> None:
        events = self.run_listener(
            52111,
            "HB-1",
            [
                {"type": "evt_strike", "hub_sn": "HB-2", "evt": [1, 5, 100]},
                {"type": "evt_precip", "hub_sn": "HB-1", "evt": [1]},
            ],
        )
        self.assertEqual(self.drain(events), ["evt_precip"])

    def test_full_queue_does_not_block_the_listener(self) -> None:
        events: queue.Queue = queue.Queue(maxsize=1)
        cache = LatestCache()
        stop = threading.Event()
        port = 52112
        thread = threading.Thread(
            target=listen_forever,
            args=(cache, LatestCache(), self.db_path, port, "", "imperial", stop),
            kwargs={"event_queue": events},
            daemon=True,
        )
        thread.start()
        time.sleep(0.3)
        obs = [1785177022, 0.37, 0.65, 0.91, 357, 15, 851.22, 30.36, 39.16,
               58662, 5.2, 489, 0.0, 0, 0, 0, 2.647, 1]
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            for _ in range(3):
                client.sendto(json.dumps({"type": "evt_precip", "evt": [1]}).encode(), ("127.0.0.1", port))
            client.sendto(json.dumps({"type": "obs_st", "obs": [obs]}).encode(), ("127.0.0.1", port))
            client.close()
            time.sleep(0.3)
        finally:
            stop.set()
            thread.join(timeout=5)
        self.assertEqual(events.qsize(), 1)
        self.assertIsNotNone(cache.get())  # the obs_st after the overflow was still processed


if __name__ == "__main__":
    unittest.main()
