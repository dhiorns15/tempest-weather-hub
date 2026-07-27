"""HTTP server: current-conditions + history API, plus the static site.

/healthz, /api/current, and /api/history are handled directly; anything else
is resolved against the static files directory (index.html for "/", real
404s for missing files, and a path-traversal guard for anything that escapes
it).
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .api_keys import verify_key
from .cache import LatestCache
from .db import query_history
from .kiosk_mapping import build_kiosk_response
from .widgets import render_current_widget

logger = logging.getLogger("weatherhub")

_RAW_RESOLUTION_MAX_RANGE_SECONDS = 31 * 86400
_DEFAULT_RANGE_SECONDS = 86400


def _resolve_static_path(static_dir: Path, url_path: str) -> Path | None:
    """Map a URL path onto a file under static_dir, or None if disallowed/missing."""
    rel = url_path.lstrip("/") or "index.html"
    candidate = (static_dir / rel).resolve()

    if os.path.commonpath([str(static_dir), str(candidate)]) != str(static_dir):
        return None  # path traversal attempt (e.g. "../../etc/passwd")

    if candidate.is_dir():
        candidate = candidate / "index.html"

    return candidate if candidate.is_file() else None


def make_handler(
    cache: LatestCache,
    forecast_cache: LatestCache,
    station_health_cache: LatestCache,
    db_path: str,
    static_dir: Path,
    cloud_configured: bool,
    udp_configured: bool,
) -> type[BaseHTTPRequestHandler]:
    static_dir = static_dir.resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            logger.info("%s - %s", self.address_string(), format % args)

        def _write_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _write_file(self, path: Path) -> None:
            content_type, _ = mimetypes.guess_type(str(path))
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _write_html(self, status: int, html_body: str) -> None:
            body = html_body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _bearer_token(self) -> str | None:
            header = self.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return None
            return header[len("Bearer ") :].strip() or None

        def _handle_history(self, query: dict[str, list[str]]) -> None:
            resolution = query.get("resolution", ["raw"])[0]
            if resolution not in ("raw", "hourly", "daily"):
                self._write_json(
                    400, {"error": "resolution must be raw, hourly, or daily"}
                )
                return

            now = int(time.time())
            try:
                end = int(query["end"][0]) if "end" in query else now
                start = (
                    int(query["start"][0])
                    if "start" in query
                    else end - _DEFAULT_RANGE_SECONDS
                )
            except ValueError:
                self._write_json(400, {"error": "start/end must be unix timestamps"})
                return

            if start >= end:
                self._write_json(400, {"error": "start must be before end"})
                return

            if (
                resolution == "raw"
                and (end - start) > _RAW_RESOLUTION_MAX_RANGE_SECONDS
            ):
                self._write_json(
                    400,
                    {
                        "error": (
                            "raw resolution is limited to a 31-day range; "
                            "use resolution=hourly or daily for longer ranges"
                        )
                    },
                )
                return

            rows = query_history(db_path, start, end, resolution)
            self._write_json(
                200,
                {"start": start, "end": end, "resolution": resolution, "observations": rows},
            )

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = urllib.parse.parse_qs(parsed.query)

            if path == "/healthz":
                self._write_json(200, {"status": "ok"})
                return

            if path == "/api/current":
                snapshot = cache.get()
                if snapshot is None:
                    self._write_json(503, {"error": "weather data not yet available"})
                    return
                self._write_json(200, snapshot)
                return

            if path == "/api/capabilities":
                self._write_json(
                    200, {"cloud_configured": cloud_configured, "udp_configured": udp_configured}
                )
                return

            if path == "/api/forecast":
                if not cloud_configured:
                    self._write_json(
                        503,
                        {
                            "error": (
                                "forecast requires TEMPEST_STATION_ID and "
                                "TEMPEST_TOKEN to be configured"
                            )
                        },
                    )
                    return
                snapshot = forecast_cache.get()
                if snapshot is None:
                    self._write_json(503, {"error": "forecast data not yet available"})
                    return
                self._write_json(200, snapshot)
                return

            if path == "/api/station-health":
                snapshot = station_health_cache.get()
                if snapshot is None:
                    self._write_json(
                        503,
                        {
                            "error": (
                                "station health data not yet available "
                                "(requires TEMPEST_UDP_ENABLED=true)"
                            )
                        },
                    )
                    return
                self._write_json(200, snapshot)
                return

            if path == "/api/history":
                token = self._bearer_token()
                if token is None or not verify_key(db_path, token):
                    self._write_json(401, {"error": "missing or invalid API key"})
                    return
                self._handle_history(query)
                return

            if path == "/site/history":
                # Same query/response shape as /api/history, unauthenticated.
                # This is what the bundled site's own history page calls -
                # baking an admin-issued key into public page JS would leak
                # it, so the site gets its own unauthenticated internal route
                # instead of the key-gated public API.
                self._handle_history(query)
                return

            if path == "/kiosk/weather":
                # OpenWeatherMap-shaped output for Immich Kiosk's
                # custom_weather_url, replacing the standalone
                # tempest-kiosk-weather-bridge service. Needs the cloud
                # source specifically - icon/conditions text aren't in the
                # local UDP protocol at all, so there's no meaningful
                # degraded response to give a UDP-only setup here.
                if not cloud_configured:
                    self._write_json(
                        503,
                        {
                            "error": (
                                "Kiosk output requires TEMPEST_STATION_ID and "
                                "TEMPEST_TOKEN to be configured"
                            )
                        },
                    )
                    return
                snapshot = cache.get()
                if snapshot is None:
                    self._write_json(503, {"error": "weather data not yet available"})
                    return
                self._write_json(200, build_kiosk_response(snapshot, forecast_cache.get() or {}))
                return

            if path == "/widget/current":
                theme = query.get("theme", ["light"])[0]
                if theme not in ("light", "dark"):
                    theme = "light"
                self._write_html(200, render_current_widget(cache.get(), theme))
                return

            resolved = _resolve_static_path(static_dir, path)
            if resolved is None:
                self._write_json(404, {"error": "not found"})
                return
            self._write_file(resolved)

    return Handler


def make_server(
    cache: LatestCache,
    forecast_cache: LatestCache,
    station_health_cache: LatestCache,
    db_path: str,
    static_dir: Path,
    port: int,
    cloud_configured: bool,
    udp_configured: bool = False,
) -> ThreadingHTTPServer:
    handler_cls = make_handler(
        cache,
        forecast_cache,
        station_health_cache,
        db_path,
        static_dir,
        cloud_configured,
        udp_configured,
    )
    return ThreadingHTTPServer(("0.0.0.0", port), handler_cls)  # noqa: S104
