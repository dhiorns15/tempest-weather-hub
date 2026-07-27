import os
import tempfile
import unittest

from weatherhub.dotenv import load_dotenv


class TestLoadDotenv(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_backup)

    def _write_env_file(self, contents: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".env")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(contents)
        self.addCleanup(os.remove, path)
        return path

    def test_loads_simple_key_value_pairs(self) -> None:
        path = self._write_env_file("TEMPEST_STATION_ID=104000\nTEMPEST_TOKEN=abc123\n")
        os.environ.pop("TEMPEST_STATION_ID", None)
        os.environ.pop("TEMPEST_TOKEN", None)

        load_dotenv(path)

        self.assertEqual(os.environ["TEMPEST_STATION_ID"], "104000")
        self.assertEqual(os.environ["TEMPEST_TOKEN"], "abc123")

    def test_ignores_blank_lines_and_comments(self) -> None:
        path = self._write_env_file(
            "\n# a comment\nTEMPEST_UNITS=metric\n   \n# another\n"
        )
        os.environ.pop("TEMPEST_UNITS", None)

        load_dotenv(path)

        self.assertEqual(os.environ["TEMPEST_UNITS"], "metric")

    def test_strips_surrounding_quotes(self) -> None:
        path = self._write_env_file('TEMPEST_TOKEN="quoted-value"\nPORT=\'8081\'\n')
        os.environ.pop("TEMPEST_TOKEN", None)
        os.environ.pop("PORT", None)

        load_dotenv(path)

        self.assertEqual(os.environ["TEMPEST_TOKEN"], "quoted-value")
        self.assertEqual(os.environ["PORT"], "8081")

    def test_existing_environment_variable_takes_precedence(self) -> None:
        path = self._write_env_file("TEMPEST_STATION_ID=from-file\n")
        os.environ["TEMPEST_STATION_ID"] = "from-real-env"

        load_dotenv(path)

        self.assertEqual(os.environ["TEMPEST_STATION_ID"], "from-real-env")

    def test_missing_file_is_a_noop(self) -> None:
        # Should not raise.
        load_dotenv("/nonexistent/path/.env")


if __name__ == "__main__":
    unittest.main()
