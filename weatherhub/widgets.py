"""Renders embeddable widget HTML via string.Template (no templating engine).

Widgets are meant to be dropped into a third-party page via <iframe>, so each
one is a fully self-contained HTML document with inline CSS - no dependency
on /css/site.css or any other asset loading correctly in someone else's page.
"""

from __future__ import annotations

import html
from string import Template
from typing import Any

_ICONS = {
    "clear-day": "☀️",
    "clear-night": "🌙",
    "cloudy": "☁️",
    "partly-cloudy-day": "⛅",
    "partly-cloudy-night": "🌥️",
    "foggy": "🌫️",
    "possibly-rainy-day": "🌦️",
    "possibly-rainy-night": "🌧️",
    "rainy": "🌧️",
    "possibly-sleet-day": "🌨️",
    "possibly-sleet-night": "🌨️",
    "sleet": "🌨️",
    "possibly-snow-day": "🌨️",
    "possibly-snow-night": "🌨️",
    "snow": "❄️",
    "possibly-thunderstorm-day": "⛈️",
    "possibly-thunderstorm-night": "⛈️",
    "thunderstorm": "⛈️",
    "windy": "💨",
}
_DEFAULT_ICON = "🌡️"

_THEMES = {
    "light": {"bg": "#ffffff", "text": "#1a1d21", "muted": "#5c6570", "border": "#e3e7ec"},
    "dark": {"bg": "#1d2025", "text": "#eef1f5", "muted": "#9aa4b2", "border": "#2b2f36"},
}

_CURRENT_TEMPLATE = Template(
    """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Weather</title>
<style>
  * { box-sizing: border-box; }
  html, body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    background: $bg;
    color: $text;
  }
  .widget {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.8rem 1rem;
    border: 1px solid $border;
    border-radius: 12px;
  }
  .icon { font-size: 1.8rem; line-height: 1; }
  .temp { font-size: 1.6rem; font-weight: 600; line-height: 1.2; }
  .conditions { font-size: 0.8rem; color: $muted; }
</style>
</head>
<body>
  <div class="widget">
    <span class="icon">$icon</span>
    <div>
      <div class="temp">$temp&deg;</div>
      <div class="conditions">$conditions</div>
    </div>
  </div>
</body>
</html>
"""
)


def render_current_widget(snapshot: dict[str, Any] | None, theme: str = "light") -> str:
    colors = _THEMES.get(theme, _THEMES["light"])

    if snapshot is None:
        icon, temp_display, conditions = _DEFAULT_ICON, "--", "Waiting for data…"
    else:
        temp = snapshot.get("air_temperature")
        icon = _ICONS.get(snapshot.get("icon"), _DEFAULT_ICON)
        temp_display = str(round(temp)) if temp is not None else "--"
        conditions = html.escape(snapshot.get("conditions") or "")

    return _CURRENT_TEMPLATE.substitute(
        bg=colors["bg"],
        text=colors["text"],
        muted=colors["muted"],
        border=colors["border"],
        icon=icon,
        temp=temp_display,
        conditions=conditions,
    )
