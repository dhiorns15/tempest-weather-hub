import unittest

from weatherhub.widgets import render_current_widget


class TestRenderCurrentWidget(unittest.TestCase):
    def test_renders_temperature_and_conditions(self) -> None:
        html_out = render_current_widget(
            {"air_temperature": 21.6, "conditions": "Clear", "icon": "clear-day"}
        )

        self.assertIn("22", html_out)  # rounded
        self.assertIn("Clear", html_out)
        self.assertIn("☀️", html_out)

    def test_missing_snapshot_shows_placeholder(self) -> None:
        html_out = render_current_widget(None)

        self.assertIn("Waiting for data", html_out)
        self.assertIn("--", html_out)

    def test_unknown_icon_falls_back_to_default(self) -> None:
        html_out = render_current_widget(
            {"air_temperature": 10, "conditions": "Mystery", "icon": "not-a-real-icon"}
        )

        self.assertIn("🌡️", html_out)

    def test_dark_theme_changes_colors(self) -> None:
        light = render_current_widget({"air_temperature": 10}, theme="light")
        dark = render_current_widget({"air_temperature": 10}, theme="dark")

        self.assertIn("#ffffff", light)
        self.assertIn("#1d2025", dark)
        self.assertNotEqual(light, dark)

    def test_conditions_text_is_html_escaped(self) -> None:
        html_out = render_current_widget(
            {"air_temperature": 10, "conditions": "<script>alert(1)</script>"}
        )

        self.assertNotIn("<script>", html_out)
        self.assertIn("&lt;script&gt;", html_out)

    def test_unknown_theme_falls_back_to_light(self) -> None:
        html_out = render_current_widget({"air_temperature": 10}, theme="neon")

        self.assertIn("#ffffff", html_out)


if __name__ == "__main__":
    unittest.main()
