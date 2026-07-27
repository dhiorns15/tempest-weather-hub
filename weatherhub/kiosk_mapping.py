"""Maps this hub's cached current-conditions + forecast onto the
OpenWeatherMap-shaped JSON that Immich Kiosk's custom weather provider
expects — ported from tempest-kiosk-weather-bridge's mapping.py so Kiosk can
point at this hub directly instead of running that standalone bridge.
"""

from __future__ import annotations

from typing import Any

# Tempest's Dark-Sky-style icon strings -> OpenWeatherMap numeric condition-code
# buckets used by Kiosk's own icon lookup (falls back to 800/clear if unknown).
_ICON_TO_OWM_ID = {
    "clear-day": 800,
    "clear-night": 800,
    "cloudy": 804,
    "partly-cloudy-day": 804,
    "partly-cloudy-night": 804,
    "foggy": 741,
    "possibly-rainy-day": 500,
    "possibly-rainy-night": 500,
    "rainy": 500,
    "possibly-sleet-day": 611,
    "possibly-sleet-night": 611,
    "sleet": 611,
    "possibly-snow-day": 600,
    "possibly-snow-night": 600,
    "snow": 600,
    "possibly-thunderstorm-day": 200,
    "possibly-thunderstorm-night": 200,
    "thunderstorm": 200,
    "windy": 771,
}

_DEFAULT_OWM_ID = 800


def tempest_icon_to_owm_id(icon: str | None) -> int:
    """Translate a Tempest forecast icon string to an OWM condition-code bucket."""
    return _ICON_TO_OWM_ID.get(icon or "", _DEFAULT_OWM_ID)


def _noon_local_timestamp(day_start_local: int) -> int:
    """Given a Tempest day_start_local (unix timestamp for local midnight),
    return the unix timestamp 12 hours later (local noon that same day).
    """
    return day_start_local + 12 * 3600


def build_kiosk_response(
    current_conditions: dict[str, Any], forecast: dict[str, Any]
) -> dict[str, Any]:
    """Build the combined OpenWeatherMap-shaped JSON body Kiosk expects.

    Takes this hub's own cached current-conditions (which already carries
    location_name — see poller.py) and forecast snapshots, rather than a raw
    Tempest API response.
    """
    response: dict[str, Any] = {
        "name": current_conditions.get("location_name", ""),
        "weather": [
            {
                "id": tempest_icon_to_owm_id(current_conditions.get("icon")),
                "description": current_conditions.get("conditions", ""),
            }
        ],
        "main": {
            "temp": current_conditions.get("air_temperature", 0),
            "temp_min": current_conditions.get("air_temperature", 0),
            "temp_max": current_conditions.get("air_temperature", 0),
            "humidity": current_conditions.get("relative_humidity", 0),
        },
        "wind": {
            "speed": current_conditions.get("wind_avg", 0),
            "deg": current_conditions.get("wind_direction", 0),
        },
        "dt": current_conditions.get("time", 0),
    }

    list_items: list[dict[str, Any]] = []

    # Hourly entries give Kiosk's "next 24h high/low" scan the fine-grained,
    # real-timestamped data it needs (it filters by actual elapsed time, not date).
    for hour in forecast.get("hourly", []):
        temp = hour.get("air_temperature", 0)
        list_items.append(
            {
                "dt": hour.get("time", 0),
                "main": {"temp_max": temp, "temp_min": temp},
                "weather": [{"id": tempest_icon_to_owm_id(hour.get("icon"))}],
            }
        )

    # Daily entries (timestamped at local noon) guarantee the 3-day forecast
    # strip has data even for days beyond hourly's coverage window.
    for day in forecast.get("daily", []):
        day_start = day.get("day_start_local", 0)
        list_items.append(
            {
                "dt": _noon_local_timestamp(day_start),
                "main": {
                    "temp_max": day.get("air_temp_high", 0),
                    "temp_min": day.get("air_temp_low", 0),
                },
                "weather": [{"id": tempest_icon_to_owm_id(day.get("icon"))}],
            }
        )

    response["list"] = list_items

    return response
