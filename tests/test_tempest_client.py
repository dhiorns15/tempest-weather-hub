import io
import json
import unittest
import urllib.error
import urllib.parse
from unittest.mock import MagicMock, patch

from weatherhub.tempest_client import TempestError, fetch_tempest


class _FakeResponse(io.BytesIO):
    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class TestFetchTempest(unittest.TestCase):
    @patch("weatherhub.tempest_client.urllib.request.urlopen")
    def test_success_builds_correct_request_and_parses_json(
        self, mock_urlopen: MagicMock
    ) -> None:
        payload = {"current_conditions": {"conditions": "Clear"}}
        mock_urlopen.return_value = _FakeResponse(json.dumps(payload).encode("utf-8"))

        result = fetch_tempest("1234", "secret-token", "metric")

        self.assertEqual(result, payload)

        sent_request = mock_urlopen.call_args[0][0]
        parsed = urllib.parse.urlparse(sent_request.full_url)
        query = urllib.parse.parse_qs(parsed.query)

        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "swd.weatherflow.com")
        self.assertEqual(parsed.path, "/swd/rest/better_forecast")
        self.assertEqual(query["station_id"], ["1234"])
        self.assertEqual(query["token"], ["secret-token"])
        self.assertEqual(query["units_temp"], ["c"])
        self.assertEqual(query["units_wind"], ["mps"])

    @patch("weatherhub.tempest_client.urllib.request.urlopen")
    def test_imperial_units_mapped_correctly(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _FakeResponse(b"{}")

        fetch_tempest("1234", "secret-token", "imperial")

        sent_request = mock_urlopen.call_args[0][0]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(sent_request.full_url).query)

        self.assertEqual(query["units_temp"], ["f"])
        self.assertEqual(query["units_wind"], ["mph"])
        self.assertEqual(query["units_pressure"], ["inhg"])

    @patch("weatherhub.tempest_client.urllib.request.urlopen")
    def test_http_error_raises_tempest_error(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://swd.weatherflow.com/swd/rest/better_forecast",
            code=401,
            msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b'{"status":{"status_code":401}}'),
        )

        with self.assertRaises(TempestError):
            fetch_tempest("1234", "bad-token", "metric")

    @patch("weatherhub.tempest_client.urllib.request.urlopen")
    def test_invalid_json_raises_tempest_error(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _FakeResponse(b"not json")

        with self.assertRaises(TempestError):
            fetch_tempest("1234", "secret-token", "metric")


if __name__ == "__main__":
    unittest.main()
