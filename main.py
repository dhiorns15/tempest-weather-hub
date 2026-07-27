#!/usr/bin/env python3
"""Entrypoint for the Tempest Weather Hub.

Reads configuration from environment variables and starts whichever data
source(s) are configured - at least one of Tempest cloud (REST) or the local
UDP broadcast is required, but not both. Serves the site + JSON API over
HTTP regardless of which source(s) are active; forecast and Kiosk output
specifically require the cloud source (UDP has no equivalent for them).

Cloud (REST) env vars - both required together, or omit both to run UDP-only:
  TEMPEST_STATION_ID   Tempest station ID
  TEMPEST_TOKEN        Tempest personal access token

Optional env vars:
  TEMPEST_UNITS            "metric" or "imperial" (default: imperial)
  PORT                     HTTP port to listen on (default: 8080)
  POLL_INTERVAL_SECONDS    seconds between Tempest polls (default: 300)
  DB_PATH                  path to the SQLite history file (default: data/weather.db)
  STATIC_DIR               path to the static site directory (default: static)
  TEMPEST_UDP_ENABLED      "true" to also/instead listen for the station's
                           local UDP broadcast (same LAN only) for current
                           conditions + history (default: false)
  TEMPEST_UDP_PORT         UDP port to listen on (default: 50222) - Tempest
                           hubs always broadcast to 50222, so there's rarely
                           a reason to change this
  TEMPEST_HUB_SERIAL       optional hub_sn filter, e.g. "HB-00118618" (see
                           /healthz logs or WeatherFlow app); blank accepts
                           broadcasts from any hub on the network

A .env file in the working directory is also loaded automatically (copy
.env.example to .env and fill in your values). Real environment variables
always take precedence over values from .env.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from pathlib import Path

from weatherhub.cache import LatestCache
from weatherhub.db import init_db
from weatherhub.dotenv import load_dotenv
from weatherhub.poller import poll_forever
from weatherhub.server import make_server
from weatherhub.udp_listener import listen_forever as udp_listen_forever

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("weatherhub")


def main() -> int:
    load_dotenv()

    station_id = os.environ.get("TEMPEST_STATION_ID", "")
    token = os.environ.get("TEMPEST_TOKEN", "")
    cloud_configured = bool(station_id and token)

    udp_enabled = os.environ.get("TEMPEST_UDP_ENABLED", "false").strip().lower() in (
        "true",
        "1",
        "yes",
    )

    if not cloud_configured and not udp_enabled:
        logger.error(
            "Configure at least one data source: TEMPEST_STATION_ID + "
            "TEMPEST_TOKEN (cloud), and/or TEMPEST_UDP_ENABLED=true (local network)"
        )
        return 1

    unit_system = os.environ.get("TEMPEST_UNITS", "imperial")
    port = int(os.environ.get("PORT", "8080"))
    poll_interval_seconds = float(os.environ.get("POLL_INTERVAL_SECONDS", "300"))
    db_path = os.environ.get("DB_PATH", "data/weather.db")
    static_dir = Path(os.environ.get("STATIC_DIR", "static"))

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)

    cache = LatestCache()
    forecast_cache = LatestCache()
    station_health_cache = LatestCache()
    stop_event = threading.Event()

    if cloud_configured:
        poll_thread = threading.Thread(
            target=poll_forever,
            args=(
                cache,
                forecast_cache,
                db_path,
                station_id,
                token,
                unit_system,
                poll_interval_seconds,
                stop_event,
            ),
            daemon=True,
        )
        poll_thread.start()
    else:
        logger.info("TEMPEST_STATION_ID/TEMPEST_TOKEN not set - running UDP-only")

    if udp_enabled:
        udp_port = int(os.environ.get("TEMPEST_UDP_PORT", "50222"))
        hub_serial = os.environ.get("TEMPEST_HUB_SERIAL", "")
        udp_thread = threading.Thread(
            target=udp_listen_forever,
            args=(
                cache,
                station_health_cache,
                db_path,
                udp_port,
                hub_serial,
                unit_system,
                stop_event,
            ),
            daemon=True,
        )
        udp_thread.start()

    server = make_server(
        cache,
        forecast_cache,
        station_health_cache,
        db_path,
        static_dir,
        port,
        cloud_configured,
        udp_enabled,
    )

    def handle_shutdown(signum: int, frame: object) -> None:
        logger.info("Shutting down (signal %s)", signum)
        stop_event.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    logger.info("Serving Tempest Weather Hub on port %d", port)
    server.serve_forever()
    server.server_close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
