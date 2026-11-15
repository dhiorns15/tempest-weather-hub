"""HTTP server: current-conditions + history API, plus the static site.

/healthz, /api/current, and /api/history are handled directly; anything else
is resolved against the static files directory (index.html for "/", real
404s for missing files, and a path-traversal guard for anything that escapes
it).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import mimetypes
import os
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .alert_store import recent_alerts
from .api_keys import verify_key
from .cache import LatestCache
from .db import history_columns, query_history
from .kiosk_mapping import build_kiosk_response
from .records import RecordsCache
from .station_timezone import DEFAULT_TIMEZONE, StationTimezone
from .tempest_client import display_units
from .widgets import load_icons_js, render_current_widget
from .wind_rose import wind_rose

logger = logging.getLogger("weatherhub")

_RAW_RESOLUTION_MAX_RANGE_SECONDS = 31 * 86400
_DEFAULT_RANGE_SECONDS = 86400

# Types mimetypes.guess_type can't be trusted with: its table also reads the
# host's own (the Windows registry, /etc/mime.types), which may not know them.
_CONTENT_TYPE_OVERRIDES = {
    ".webmanifest": "application/manifest+json",
}


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
    unit_system: str = "imperial",
    station_timezone: StationTimezone | None = None,
    alerts_enabled: bool = False,
) -> type[BaseHTTPRequestHandler]:
    static_dir = static_dir.resolve()
    units = display_units(unit_system)
    records_cache = RecordsCache()

    def timezone_name() -> str:
        return station_timezone.name if station_timezone is not None else DEFAULT_TIMEZONE

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            logger.info("%s - %s", self.address_string(), format % args)

        def _write_json(
            self, status: int, payload: dict[str, Any], disposition: str | None = None
        ) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if disposition:
                self.send_header("Content-Disposition", disposition)
            self.end_headers()
            self.wfile.write(body)

        def _write_csv(
            self,
            status: int,
            columns: list[str],
            rows: list[dict[str, Any]],
            disposition: str | None,
        ) -> None:
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(columns)
            writer.writerows([row.get(column) for column in columns] for row in rows)
            body = buffer.getvalue().encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if disposition:
                self.send_header("Content-Disposition", disposition)
            self.end_headers()
            self.wfile.write(body)

        def _write_file(self, path: Path) -> None:
            content_type = _CONTENT_TYPE_OVERRIDES.get(path.suffix.lower())
            if content_type is None:
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

            export_format = query.get("format", ["json"])[0]
            if export_format not in ("json", "csv"):
                self._write_json(400, {"error": "format must be json or csv"})
                return

            download = query.get("download", ["0"])[0].lower() in ("1", "true", "yes")

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

            tz_name = timezone_name()
            rows = query_history(db_path, start, end, resolution, tz_name)

            disposition = None
            if download:
                filename = f"weather-history-{start}-{end}-{resolution}.{export_format}"
                disposition = f'attachment; filename="{filename}"'

            if export_format == "csv":
                self._write_csv(200, history_columns(resolution), rows, disposition)
                return

            self._write_json(
                200,
                {
                    "start": start,
                    "end": end,
                    "resolution": resolution,
                    "timezone": tz_name,
                    "observations": rows,
                },
                disposition,
            )

        def _handle_wind_rose(self, query: dict[str, list[str]]) -> None:
            # Same start/end handling as history: unix timestamps, the last
            # 24 hours by default.
            now = int(time.time())
            try:
                end = int(query["end"][0]) if "end" in query else now
                start = (
                    int(query["start"][0]) if "start" in query else end - _DEFAULT_RANGE_SECONDS
                )
            except ValueError:
                self._write_json(400, {"error": "start/end must be unix timestamps"})
                return
            if start >= end:
                self._write_json(400, {"error": "start must be before end"})
                return
            self._write_json(200, wind_rose(db_path, start, end, units["system"]))

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
                    200,
                    {
                        "cloud_configured": cloud_configured,
                        "udp_configured": udp_configured,
                        "units": units,
                        "timezone": timezone_name(),
                    },
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

            if path == "/api/alerts":
                try:
                    limit = min(max(int(query.get("limit", ["50"])[0]), 1), 200)
                except ValueError:
                    self._write_json(400, {"error": "limit must be an integer"})
                    return
                self._write_json(
                    200, {"enabled": alerts_enabled, "alerts": recent_alerts(db_path, limit)}
                )
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

            if path == "/site/records":
                # Record highs/lows and "on this day" comparisons for the
                # History page, over station-local days (see records.py).
                self._write_json(200, records_cache.get(db_path, timezone_name(), units))
                return

            if path == "/site/wind-rose":
                self._handle_wind_rose(query)
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
                if theme not in ("light", "dark", "auto"):
                    theme = "light"
                self._write_html(
                    200,
                    render_current_widget(
                        cache.get(), theme, units, load_icons_js(static_dir)
                    ),
                )
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
    unit_system: str = "imperial",
    station_timezone: StationTimezone | None = None,
    alerts_enabled: bool = False,
) -> ThreadingHTTPServer:
    handler_cls = make_handler(
        cache,
        forecast_cache,
        station_health_cache,
        db_path,
        static_dir,
        cloud_configured,
        udp_configured,
        unit_system=unit_system,
        station_timezone=station_timezone,
        alerts_enabled=alerts_enabled,
    )
    return ThreadingHTTPServer(("0.0.0.0", port), handler_cls)  # noqa: S104
