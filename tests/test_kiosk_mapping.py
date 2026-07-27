import unittest

from weatherhub.kiosk_mapping import build_kiosk_response, tempest_icon_to_owm_id


class TestTempestIconToOwmId(unittest.TestCase):
    def test_known_icons(self) -> None:
        self.assertEqual(tempest_icon_to_owm_id("clear-day"), 800)
        self.assertEqual(tempest_icon_to_owm_id("rainy"), 500)
        self.assertEqual(tempest_icon_to_owm_id("thunderstorm"), 200)
        self.assertEqual(tempest_icon_to_owm_id("snow"), 600)

    def test_unknown_and_missing_icon_defaults_to_clear(self) -> None:
        self.assertEqual(tempest_icon_to_owm_id("not-a-real-icon"), 800)
        self.assertEqual(tempest_icon_to_owm_id(None), 800)


class TestBuildKioskResponse(unittest.TestCase):
    def test_current_conditions_fields(self) -> None:
        current = {
            "location_name": "Tower 1",
            "icon": "partly-cloudy-day",
            "conditions": "Partly Cloudy",
            "air_temperature": 21.5,
            "relative_humidity": 55,
            "wind_avg": 3.2,
            "wind_direction": 180,
            "time": 1_700_000_000,
        }

        response = build_kiosk_response(current, {})

        self.assertEqual(response["name"], "Tower 1")
        self.assertEqual(response["weather"], [{"id": 804, "description": "Partly Cloudy"}])
        self.assertEqual(
            response["main"],
            {"temp": 21.5, "temp_min": 21.5, "temp_max": 21.5, "humidity": 55},
        )
        self.assertEqual(response["wind"], {"speed": 3.2, "deg": 180})
        self.assertEqual(response["dt"], 1_700_000_000)
        self.assertEqual(response["list"], [])

    def test_forecast_list_includes_hourly_and_daily(self) -> None:
        forecast = {
            "hourly": [
                {"time": 1_700_003_600, "air_temperature": 25.0, "icon": "clear-day"},
            ],
            "daily": [
                {
                    "day_start_local": 1_700_000_000,
                    "air_temp_high": 30.0,
                    "air_temp_low": 18.0,
                    "icon": "rainy",
                },
            ],
        }

        response = build_kiosk_response({}, forecast)

        self.assertEqual(len(response["list"]), 2)

        hourly_item = response["list"][0]
        self.assertEqual(hourly_item["dt"], 1_700_003_600)
        self.assertEqual(hourly_item["main"], {"temp_max": 25.0, "temp_min": 25.0})
        self.assertEqual(hourly_item["weather"], [{"id": 800}])

        daily_item = response["list"][1]
        self.assertEqual(daily_item["dt"], 1_700_000_000 + 12 * 3600)
        self.assertEqual(daily_item["main"], {"temp_max": 30.0, "temp_min": 18.0})
        self.assertEqual(daily_item["weather"], [{"id": 500}])

    def test_missing_forecast_sections_produce_empty_list(self) -> None:
        response = build_kiosk_response({}, {})
        self.assertEqual(response["list"], [])

    def test_fractional_humidity_and_wind_direction_are_coerced_to_int(self) -> None:
        # Regression test: UDP-sourced fields can be unrounded floats (e.g.
        # 36.81), which Kiosk's Go structs reject outright since they type
        # humidity/deg/dt as strict ints - any decimal point fails to
        # unmarshal, even something like 36.0.
        current = {
            "relative_humidity": 36.81,
            "wind_direction": 180.0,
            "time": 1_700_000_000.0,
        }

        response = build_kiosk_response(current, {})

        self.assertEqual(response["main"]["humidity"], 37)
        self.assertIsInstance(response["main"]["humidity"], int)
        self.assertEqual(response["wind"]["deg"], 180)
        self.assertIsInstance(response["wind"]["deg"], int)
        self.assertEqual(response["dt"], 1_700_000_000)
        self.assertIsInstance(response["dt"], int)


if __name__ == "__main__":
    unittest.main()
