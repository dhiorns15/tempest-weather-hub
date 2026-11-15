"""Renders embeddable widget HTML via string.Template (no templating engine).

Widgets are meant to be dropped into a third-party page via <iframe>, so each
one is a fully self-contained HTML document with inline CSS and JS - no
dependency on /css/site.css or any other asset loading correctly in someone
else's page, and no requests of its own.

The weather icon artwork lives in one place, static/js/icons.js, which the
site loads as a script. The widget inlines that same file's source and calls
its weatherIcon() from a small inline script, so the widget and the site can
never drift apart. Text is rendered server-side, so a frame that blocks
scripts still shows everything except the icon.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from string import Template
from typing import Any

from .tempest_client import display_units

# Colour tokens copied from static/css/site.css (the neutral, condition-free
# page theme): text, muted text and the --ic-* icon colours, plus the plain
# sky gradient as the card background. Keep these in step with site.css.
_LIGHT_TOKENS = {
    "--bg": "linear-gradient(180deg, #eef2f7 0%, #dde5ee 100%)",
    "--border": "rgba(16, 40, 80, 0.14)",
    "--text": "#101a28",
    "--text-muted": "#4d5b70",
    "--ic-sun": "#f5a623",
    "--ic-moon": "#7f8fd8",
    "--ic-cloud": "#b9c6d8",
    "--ic-cloud-dark": "#8b9ab0",
    "--ic-rain": "#2a78d6",
    "--ic-snow": "#6fb2e6",
    "--ic-bolt": "#f0a400",
    "--ic-line": "#7d8ca3",
}

_DARK_TOKENS = {
    "--bg": "linear-gradient(180deg, #0f1420 0%, #171d2b 100%)",
    "--border": "rgba(255, 255, 255, 0.1)",
    "--text": "#f1f5fb",
    "--text-muted": "#a9b6c9",
    "--ic-sun": "#ffc83d",
    "--ic-moon": "#dfe3ff",
    "--ic-cloud": "#dde5f0",
    "--ic-cloud-dark": "#9aa8bc",
    "--ic-rain": "#6fb4ff",
    "--ic-snow": "#d3eaff",
    "--ic-bolt": "#ffd23f",
    "--ic-line": "#b3c0d2",
}

THEMES = ("light", "dark", "auto")

_COMPASS_POINTS = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)

# Wind decimals per unit system: whole mph is plenty, whole m/s is too coarse.
_WIND_DECIMALS = {"imperial": 0, "metric": 1}

_CURRENT_TEMPLATE = Template(
    """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Weather</title>
<style>
$tokens
  * { box-sizing: border-box; }
  html, body {
    height: 100%;
    margin: 0;
    background: transparent;
  }
  body {
    color: var(--text);
    font-family: system-ui, -apple-system, "Segoe UI Variable Text", "Segoe UI", Roboto,
      Helvetica, Arial, sans-serif;
    line-height: 1.25;
    -webkit-font-smoothing: antialiased;
  }
  .widget {
    display: flex;
    align-items: center;
    gap: 12px;
    height: 100%;
    padding: 8px 14px;
    overflow: hidden;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 16px;
  }
  .icon { flex: none; width: 46px; height: 46px; }
  .icon:empty { display: none; }
  .wx-icon { display: block; width: 100%; height: 100%; }
  .body { flex: 1; min-width: 0; }
  .temp {
    font-size: 30px;
    font-weight: 300;
    line-height: 1;
    letter-spacing: -0.03em;
    white-space: nowrap;
  }
  .unit {
    margin-left: 1px;
    font-size: 15px;
    font-weight: 500;
    letter-spacing: 0;
    color: var(--text-muted);
    vertical-align: 11px;
  }
  .conditions,
  .details {
    margin-top: 2px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .conditions { font-size: 13px; font-weight: 600; }
  /* One line only: readings that don't fit wrap onto a second line that is
     clipped away, so a narrow frame drops whole items rather than cutting
     one in half. Most useful first. */
  .details {
    display: flex;
    flex-wrap: wrap;
    height: 1.3em;
    font-size: 12px;
    color: var(--text-muted);
  }
  .details span { max-width: 100%; overflow: hidden; text-overflow: ellipsis; }
  .details span + span::before { content: "\\00b7"; margin: 0 6px; }
  @media (min-width: 300px) {
    .widget { gap: 14px; padding: 8px 16px; }
    .icon { width: 56px; height: 56px; }
    .temp { font-size: 34px; }
    .unit { font-size: 16px; vertical-align: 13px; }
    .conditions { font-size: 14px; }
    .details { font-size: 12.5px; }
  }
  /* Too short a frame for three lines: keep the headline readings. */
  @media (max-height: 80px) {
    .icon { width: 46px; height: 46px; }
    .temp { font-size: 30px; }
    .unit { font-size: 15px; vertical-align: 11px; }
    .details { display: none; }
  }
</style>
</head>
<body>
  <div class="widget">
    <div class="icon" id="icon"$icon_attr></div>
    <div class="body">
      <div class="temp">$temp</div>$conditions$details
    </div>
  </div>$script
</body>
</html>
"""
)


def load_icons_js(static_dir: Path) -> str | None:
    """Source of the site's icons.js, or None if it can't be read."""
    try:
        return (static_dir / "js" / "icons.js").read_text(encoding="utf-8")
    except OSError:
        return None


def _css_vars(tokens: dict[str, str]) -> str:
    return "\n".join(f"    {name}: {value};" for name, value in tokens.items())


def _theme_css(theme: str) -> str:
    light = f"  :root {{\n{_css_vars(_LIGHT_TOKENS)}\n  }}"
    dark = f"  :root {{\n{_css_vars(_DARK_TOKENS)}\n  }}"
    if theme == "dark":
        return dark
    if theme == "auto":
        return f"{light}\n  @media (prefers-color-scheme: dark) {{\n  {dark}\n  }}"
    return light


def _compass(degrees: Any) -> str:
    if degrees is None:
        return ""
    return _COMPASS_POINTS[round(degrees / 22.5) % 16]


def _wind_text(snapshot: dict[str, Any], units: dict[str, str]) -> str | None:
    speed = snapshot.get("wind_avg")
    if speed is None:
        return None
    decimals = _WIND_DECIMALS.get(units["system"], 0)
    rounded = round(speed, decimals) if decimals else round(speed)
    if rounded == 0:
        return "Wind calm"
    direction = _compass(snapshot.get("wind_direction"))
    text = f"Wind {rounded:.{decimals}f} {units['wind']}"
    return f"{text} {direction}" if direction else text


def _details(snapshot: dict[str, Any], units: dict[str, str]) -> list[str]:
    items = []
    feels_like = snapshot.get("feels_like")
    if feels_like is not None:
        items.append(f"Feels {round(feels_like)}°")
    wind = _wind_text(snapshot, units)
    if wind:
        items.append(wind)
    humidity = snapshot.get("relative_humidity")
    if humidity is not None:
        items.append(f"Humidity {round(humidity)}%")
    return items


def _inline_script(icons_js: str) -> str:
    # The file is our own, but a literal "</script" anywhere in it would end
    # the inline block early; "<\/" means the same thing to JavaScript.
    safe = re.sub(r"</(script)", r"<\\/\1", icons_js, flags=re.IGNORECASE)
    return (
        "\n  <script>\n"
        f"{safe}\n"
        "  (function () {\n"
        '    var slot = document.getElementById("icon");\n'
        "    slot.innerHTML = weatherIcon(slot.getAttribute(\"data-icon\"));\n"
        "  })();\n"
        "  </script>"
    )


def render_current_widget(
    snapshot: dict[str, Any] | None,
    theme: str = "light",
    units: dict[str, str] | None = None,
    icons_js: str | None = None,
) -> str:
    """Current-conditions widget as a complete HTML document.

    units is display_units()' output (imperial if omitted). icons_js is the
    source of static/js/icons.js; without it the widget simply has no icon.
    """
    if theme not in THEMES:
        theme = "light"
    if units is None:
        units = display_units("imperial")

    snapshot = snapshot or {}
    temp = snapshot.get("air_temperature")
    icon = snapshot.get("icon")
    conditions = snapshot.get("conditions")

    if temp is not None:
        temp_html = f'{round(temp)}<span class="unit">{html.escape(units["temp"])}</span>'
    else:
        temp_html = "--"

    if not snapshot:
        conditions = "Waiting for data…"

    conditions_html = (
        f'\n      <div class="conditions">{html.escape(conditions)}</div>' if conditions else ""
    )

    details = _details(snapshot, units)
    details_html = (
        '\n      <div class="details">'
        + "".join(f"<span>{html.escape(item)}</span>" for item in details)
        + "</div>"
        if details
        else ""
    )

    # UDP-only setups have no icon at all; leave the slot empty (it collapses)
    # rather than draw a generic cloud that may well be wrong.
    has_icon = bool(icon) and icons_js is not None
    icon_attr = f' data-icon="{html.escape(icon, quote=True)}"' if has_icon else ""
    script = _inline_script(icons_js) if has_icon else ""

    return _CURRENT_TEMPLATE.substitute(
        tokens=_theme_css(theme),
        icon_attr=icon_attr,
        temp=temp_html,
        conditions=conditions_html,
        details=details_html,
        script=script,
    )
