import re
import tempfile
import unittest
from pathlib import Path

from weatherhub.tempest_client import display_units
from weatherhub.widgets import load_icons_js, render_current_widget

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
ICONS_JS = load_icons_js(STATIC_DIR)

FULL_SNAPSHOT = {
    "air_temperature": 21.6,
    "feels_like": 20.2,
    "relative_humidity": 48.4,
    "wind_avg": 5.4,
    "wind_direction": 315,
    "conditions": "Clear",
    "icon": "clear-day",
}

# What a UDP-only setup's cache holds: numbers, no icon/conditions/feels-like.
UDP_SNAPSHOT = {
    "air_temperature": 18.2,
    "relative_humidity": 60,
    "wind_avg": 0.2,
    "wind_direction": 10,
}


class TestRenderCurrentWidget(unittest.TestCase):
    def test_renders_temperature_with_unit_and_conditions(self) -> None:
        html_out = render_current_widget(FULL_SNAPSHOT, icons_js=ICONS_JS)

        self.assertIn('22<span class="unit">°F</span>', html_out)  # rounded
        self.assertIn('<div class="conditions">Clear</div>', html_out)

    def test_metric_units(self) -> None:
        html_out = render_current_widget(
            FULL_SNAPSHOT, units=display_units("metric"), icons_js=ICONS_JS
        )

        self.assertIn('22<span class="unit">°C</span>', html_out)
        self.assertIn("Wind 5.4 m/s NW", html_out)
        self.assertNotIn("°F", html_out)
        self.assertNotIn("mph", html_out)

    def test_secondary_line(self) -> None:
        html_out = render_current_widget(FULL_SNAPSHOT, icons_js=ICONS_JS)

        self.assertIn(
            '<div class="details"><span>Feels 20°</span>'
            "<span>Wind 5 mph NW</span><span>Humidity 48%</span></div>",
            html_out,
        )

    def test_icon_comes_from_icons_js(self) -> None:
        html_out = render_current_widget(FULL_SNAPSHOT, icons_js=ICONS_JS)

        self.assertIn('data-icon="clear-day"', html_out)
        self.assertIn(ICONS_JS, html_out)
        self.assertIn('weatherIcon(slot.getAttribute("data-icon"))', html_out)

    def test_no_emoji(self) -> None:
        html_out = render_current_widget(FULL_SNAPSHOT, icons_js=ICONS_JS)

        self.assertIsNone(re.search("[\U0001f300-\U0001faff☀-➿]", html_out))

    def test_self_contained(self) -> None:
        # Embedded in third-party pages: nothing may be fetched from anywhere.
        for theme in ("light", "dark", "auto"):
            with self.subTest(theme=theme):
                html_out = render_current_widget(FULL_SNAPSHOT, theme, icons_js=ICONS_JS)
                self.assertNotRegex(html_out, r"<(script|img|link|iframe)[^>]*\s(src|href)=")
                self.assertNotIn("url(", html_out)
                self.assertNotIn("@import", html_out)
                self.assertNotIn("fetch(", html_out)

    def test_opts_out_of_dark_reader(self) -> None:
        # Dark Reader would otherwise recolour the icon fills inside the frame.
        html_out = render_current_widget(FULL_SNAPSHOT, icons_js=ICONS_JS)

        self.assertIn('<meta name="darkreader-lock">', html_out)

    def test_missing_snapshot_shows_placeholder(self) -> None:
        html_out = render_current_widget(None, icons_js=ICONS_JS)

        self.assertIn("Waiting for data", html_out)
        self.assertIn('<div class="temp">--</div>', html_out)
        self.assertNotIn("<script>", html_out)
        self.assertNotIn('class="details"', html_out)

    def test_udp_only_snapshot_has_no_icon_or_conditions(self) -> None:
        html_out = render_current_widget(UDP_SNAPSHOT, icons_js=ICONS_JS)

        self.assertIn('18<span class="unit">°F</span>', html_out)
        self.assertNotIn('class="conditions"', html_out)
        self.assertNotIn("data-icon", html_out)
        self.assertNotIn("<script>", html_out)
        self.assertIn("<span>Wind calm</span><span>Humidity 60%</span>", html_out)
        self.assertNotIn("Feels", html_out)

    def test_missing_temperature_shows_dashes_without_unit(self) -> None:
        html_out = render_current_widget({"conditions": "Clear"}, icons_js=ICONS_JS)

        self.assertIn('<div class="temp">--</div>', html_out)

    def test_without_icons_js_renders_without_icon(self) -> None:
        html_out = render_current_widget(FULL_SNAPSHOT, icons_js=None)

        self.assertNotIn("<script>", html_out)
        self.assertIn("Clear", html_out)

    def test_light_and_dark_use_site_tokens(self) -> None:
        light = render_current_widget(FULL_SNAPSHOT, theme="light")
        dark = render_current_widget(FULL_SNAPSHOT, theme="dark")

        self.assertIn("--text: #101a28;", light)
        self.assertIn("--ic-sun: #f5a623;", light)
        self.assertNotIn("prefers-color-scheme", light)
        self.assertIn("--text: #f1f5fb;", dark)
        self.assertIn("--ic-sun: #ffc83d;", dark)
        self.assertNotIn("prefers-color-scheme", dark)

    def test_auto_theme_follows_color_scheme(self) -> None:
        html_out = render_current_widget(FULL_SNAPSHOT, theme="auto")

        light_at = html_out.index("--text: #101a28;")
        media_at = html_out.index("@media (prefers-color-scheme: dark)")
        dark_at = html_out.index("--text: #f1f5fb;")
        self.assertLess(light_at, media_at)
        self.assertLess(media_at, dark_at)

    def test_unknown_theme_falls_back_to_light(self) -> None:
        html_out = render_current_widget(FULL_SNAPSHOT, theme="neon")

        self.assertIn("--text: #101a28;", html_out)
        self.assertNotIn("#f1f5fb", html_out)

    def test_conditions_text_is_html_escaped(self) -> None:
        html_out = render_current_widget(
            {"air_temperature": 10, "conditions": "<script>alert(1)</script>"}
        )

        self.assertNotIn("<script>alert", html_out)
        self.assertIn("&lt;script&gt;", html_out)

    def test_icon_name_is_attribute_escaped(self) -> None:
        html_out = render_current_widget(
            {"air_temperature": 10, "icon": '"><img src=x>'}, icons_js=ICONS_JS
        )

        self.assertNotIn("<img", html_out)
        self.assertIn('data-icon="&quot;&gt;&lt;img src=x&gt;"', html_out)

    def test_closing_script_tag_in_icons_source_is_neutralised(self) -> None:
        html_out = render_current_widget(
            FULL_SNAPSHOT, icons_js='function weatherIcon() { return "</SCRIPT>"; }'
        )

        self.assertEqual(html_out.lower().count("</script"), 1)  # just the real one
        self.assertIn("<\\/SCRIPT>", html_out)


class TestLoadIconsJs(unittest.TestCase):
    def test_reads_the_site_icons(self) -> None:
        self.assertIsNotNone(ICONS_JS)
        self.assertIn("function weatherIcon(", ICONS_JS)

    def test_missing_file_returns_none(self) -> None:
        self.assertIsNone(load_icons_js(Path(tempfile.mkdtemp())))


if __name__ == "__main__":
    unittest.main()
