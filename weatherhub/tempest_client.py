"""Client for the Tempest (WeatherFlow) "better_forecast" REST API."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_SCHEME = "https"
API_HOST = "swd.weatherflow.com"
API_PATH = "/swd/rest/better_forecast"

_UNITS_BY_SYSTEM = {
    "imperial": {
        "units_temp": "f",
        "units_wind": "mph",
        "units_pressure": "inhg",
        "units_precip": "in",
        "units_distance": "mi",
    },
    "metric": {
        "units_temp": "c",
        "units_wind": "mps",
        "units_pressure": "mb",
        "units_precip": "mm",
        "units_distance": "km",
    },
}

# How the values requested above are labelled for display (site, widget,
# /api/capabilities). Keep the two tables in step: a label here must name
# the unit Tempest actually returns for the matching request above.
_DISPLAY_UNITS_BY_SYSTEM = {
    "imperial": {
        "temp": "°F",
        "wind": "mph",
        "pressure": "inHg",
        "precip": "in",
        "distance": "mi",
    },
    "metric": {
        "temp": "°C",
        "wind": "m/s",
        "pressure": "mb",
        "precip": "mm",
        "distance": "km",
    },
}


def display_units(unit_system: str) -> dict[str, str]:
    """Display labels for the units data is stored and served in.

    Anything other than "metric" is treated as imperial, matching what
    fetch_tempest requests and what the UDP listener converts to, so the
    labels always agree with the numbers.
    """
    system = unit_system if unit_system in _DISPLAY_UNITS_BY_SYSTEM else "imperial"
    return {"system": system, **_DISPLAY_UNITS_BY_SYSTEM[system]}


class TempestError(Exception):
    """Raised when the Tempest API returns a non-2xx response or bad data."""


def fetch_tempest(
    station_id: str,
    token: str,
    unit_system: str = "imperial",
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Fetch current conditions + forecast from Tempest's better_forecast endpoint.

    Returns the parsed JSON response as a dict. Raises TempestError on any
    non-2xx HTTP status or unparseable response.
    """
    units = _UNITS_BY_SYSTEM.get(unit_system, _UNITS_BY_SYSTEM["imperial"])

    query = urllib.parse.urlencode(
        {
            "station_id": station_id,
            "token": token,
            **units,
        }
    )
    url = urllib.parse.urlunparse((API_SCHEME, API_HOST, API_PATH, "", query, ""))

    request = urllib.request.Request(url, headers={"Accept": "application/json"})

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read()
    except urllib.error.HTTPError as exc:
        body_preview = exc.read(1024).decode("utf-8", errors="replace")
        raise TempestError(
            f"Tempest API returned status {exc.code}: {body_preview}"
        ) from exc
    except urllib.error.URLError as exc:
        raise TempestError(f"Failed to reach Tempest API: {exc.reason}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise TempestError(f"Tempest API returned invalid JSON: {exc}") from exc
