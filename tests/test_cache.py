import unittest

from weatherhub.cache import LatestCache


class TestLatestCache(unittest.TestCase):
    def test_get_returns_none_before_first_set(self) -> None:
        cache = LatestCache()
        self.assertIsNone(cache.get())

    def test_set_replaces_snapshot_wholesale(self) -> None:
        cache = LatestCache()
        cache.set({"a": 1, "b": 2})
        cache.set({"c": 3})

        self.assertEqual(cache.get(), {"c": 3})

    def test_update_merges_into_existing_snapshot(self) -> None:
        cache = LatestCache()
        cache.set({"icon": "clear-day", "conditions": "Clear", "air_temperature": 20})

        cache.update({"air_temperature": 21, "wind_avg": 5})

        self.assertEqual(
            cache.get(),
            {"icon": "clear-day", "conditions": "Clear", "air_temperature": 21, "wind_avg": 5},
        )

    def test_update_with_no_prior_snapshot_starts_fresh(self) -> None:
        cache = LatestCache()
        cache.update({"air_temperature": 21})

        self.assertEqual(cache.get(), {"air_temperature": 21})


if __name__ == "__main__":
    unittest.main()
