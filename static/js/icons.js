// Inline SVG icons, drawn from a handful of shared parts so every weather
// icon looks like it belongs to the same set on every OS (emoji don't).
// Colours come from the --ic-* custom properties in site.css, so the icons
// follow the light/dark theme with no JS involved.

const WX_PARTS = {
  sun: `<circle cx="32" cy="32" r="11" fill="var(--ic-sun)"/>
    <g stroke="var(--ic-sun)" stroke-width="3.5" stroke-linecap="round">
      <path d="M32 9v6M32 49v6M9 32h6M49 32h6M15.7 15.7l4.3 4.3M44 44l4.3 4.3M15.7 48.3l4.3-4.3M44 20l4.3-4.3"/>
    </g>`,
  // Smaller sun/moon that peek out from behind a cloud's top-left shoulder.
  sunPeek: `<circle cx="22" cy="21" r="8.5" fill="var(--ic-sun)"/>
    <g stroke="var(--ic-sun)" stroke-width="3" stroke-linecap="round">
      <path d="M22 4.5v4M5.5 21h4M10.3 9.3l2.9 2.9M33.7 9.3l-2.9 2.9M10.3 32.7l2.9-2.9"/>
    </g>`,
  moon: `<path d="M38 13.5A19 19 0 1 0 50.5 40 15 15 0 0 1 38 13.5z" fill="var(--ic-moon)"/>`,
  moonPeek: `<path d="M24.5 6.5A12.5 12.5 0 1 0 34 25.5 10 10 0 0 1 24.5 6.5z" fill="var(--ic-moon)"/>`,
  cloud: (fill, transform = "") =>
    `<path d="M20 46a10 10 0 0 1 0-20 14 14 0 0 1 27-3 11.5 11.5 0 0 1 1 23z" fill="${fill}"${
      transform ? ` transform="${transform}"` : ""
    }/>`,
  rain: (xs) =>
    `<g stroke="var(--ic-rain)" stroke-width="3.5" stroke-linecap="round">${xs
      .map((x) => `<path d="M${x} 45l-3.5 9"/>`)
      .join("")}</g>`,
  snow: (points) =>
    `<g fill="var(--ic-snow)">${points
      .map(([x, y]) => `<circle cx="${x}" cy="${y}" r="2.6"/>`)
      .join("")}</g>`,
  bolt: `<path d="M35 37l-9 13h6.5l-3 11 11.5-15h-6.5l3-9z" fill="var(--ic-bolt)"/>`,
  fogLines: `<g stroke="var(--ic-line)" stroke-width="3.5" stroke-linecap="round">
      <path d="M14 46h36M20 54h24"/>
    </g>`,
  wind: `<g fill="none" stroke="var(--ic-line)" stroke-width="3.5" stroke-linecap="round">
      <path d="M8 27h28a7 7 0 1 0-7-7"/>
      <path d="M8 37h40a7 7 0 1 1-7 7"/>
      <path d="M8 47h20"/>
    </g>`,
};

// Cloud positions: "low" leaves room for a sun/moon above-left, "high"
// leaves room for precipitation underneath.
const CLOUD_LOW = "translate(6 8) scale(0.9)";
const CLOUD_HIGH = "translate(0 -8)";

function wxCompose(...parts) {
  return parts.join("");
}

const WX_ICONS = {
  "clear-day": () => WX_PARTS.sun,
  "clear-night": () => WX_PARTS.moon,
  cloudy: () =>
    wxCompose(
      WX_PARTS.cloud("var(--ic-cloud-dark)", "translate(10 -6) scale(0.8)"),
      WX_PARTS.cloud("var(--ic-cloud)", "translate(0 4)")
    ),
  "partly-cloudy-day": () =>
    wxCompose(WX_PARTS.sunPeek, WX_PARTS.cloud("var(--ic-cloud)", CLOUD_LOW)),
  "partly-cloudy-night": () =>
    wxCompose(WX_PARTS.moonPeek, WX_PARTS.cloud("var(--ic-cloud)", CLOUD_LOW)),
  foggy: () => wxCompose(WX_PARTS.cloud("var(--ic-cloud)", CLOUD_HIGH), WX_PARTS.fogLines),
  "possibly-rainy-day": () =>
    wxCompose(
      WX_PARTS.sunPeek,
      WX_PARTS.cloud("var(--ic-cloud)", "translate(6 0) scale(0.9)"),
      WX_PARTS.rain([30, 42])
    ),
  "possibly-rainy-night": () =>
    wxCompose(
      WX_PARTS.moonPeek,
      WX_PARTS.cloud("var(--ic-cloud)", "translate(6 0) scale(0.9)"),
      WX_PARTS.rain([30, 42])
    ),
  rainy: () =>
    wxCompose(WX_PARTS.cloud("var(--ic-cloud-dark)", CLOUD_HIGH), WX_PARTS.rain([24, 35, 46])),
  "possibly-sleet-day": () =>
    wxCompose(
      WX_PARTS.sunPeek,
      WX_PARTS.cloud("var(--ic-cloud)", "translate(6 0) scale(0.9)"),
      WX_PARTS.rain([30]),
      WX_PARTS.snow([[40, 50]])
    ),
  "possibly-sleet-night": () =>
    wxCompose(
      WX_PARTS.moonPeek,
      WX_PARTS.cloud("var(--ic-cloud)", "translate(6 0) scale(0.9)"),
      WX_PARTS.rain([30]),
      WX_PARTS.snow([[40, 50]])
    ),
  sleet: () =>
    wxCompose(
      WX_PARTS.cloud("var(--ic-cloud-dark)", CLOUD_HIGH),
      WX_PARTS.rain([24, 46]),
      WX_PARTS.snow([[33, 50]])
    ),
  "possibly-snow-day": () =>
    wxCompose(
      WX_PARTS.sunPeek,
      WX_PARTS.cloud("var(--ic-cloud)", "translate(6 0) scale(0.9)"),
      WX_PARTS.snow([
        [27, 49],
        [39, 49],
      ])
    ),
  "possibly-snow-night": () =>
    wxCompose(
      WX_PARTS.moonPeek,
      WX_PARTS.cloud("var(--ic-cloud)", "translate(6 0) scale(0.9)"),
      WX_PARTS.snow([
        [27, 49],
        [39, 49],
      ])
    ),
  snow: () =>
    wxCompose(
      WX_PARTS.cloud("var(--ic-cloud)", CLOUD_HIGH),
      WX_PARTS.snow([
        [22, 46],
        [33, 46],
        [44, 46],
        [27.5, 55],
        [38.5, 55],
      ])
    ),
  "possibly-thunderstorm-day": () =>
    wxCompose(
      WX_PARTS.sunPeek,
      WX_PARTS.cloud("var(--ic-cloud-dark)", "translate(6 0) scale(0.9)"),
      WX_PARTS.bolt
    ),
  "possibly-thunderstorm-night": () =>
    wxCompose(
      WX_PARTS.moonPeek,
      WX_PARTS.cloud("var(--ic-cloud-dark)", "translate(6 0) scale(0.9)"),
      WX_PARTS.bolt
    ),
  thunderstorm: () =>
    wxCompose(WX_PARTS.cloud("var(--ic-cloud-dark)", CLOUD_HIGH), WX_PARTS.bolt),
  windy: () => WX_PARTS.wind,
};

// Unknown/missing icon names (UDP-only setups have no icon at all) get a
// plain cloud rather than a broken image.
function weatherIcon(name) {
  const build = WX_ICONS[name] || (() => WX_PARTS.cloud("var(--ic-cloud)", "translate(0 4)"));
  return `<svg class="wx-icon" viewBox="0 0 64 64" aria-hidden="true">${build()}</svg>`;
}

// Small monochrome UI glyphs; they take the surrounding text colour.
const UI_ICONS = {
  drop: `<path d="M8 1.8C5.6 4.9 3.6 7.2 3.6 9.7a4.4 4.4 0 0 0 8.8 0c0-2.5-2-4.8-4.4-7.9z" fill="currentColor"/>`,
  sunrise: `<g fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
      <path d="M2 13h12M4.5 10.5a3.5 3.5 0 0 1 7 0M8 6V2M6 4l2-2 2 2"/>
    </g>`,
  sunset: `<g fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
      <path d="M2 13h12M4.5 10.5a3.5 3.5 0 0 1 7 0M8 2v4M6 4l2 2 2-2"/>
    </g>`,
};

function uiIcon(name) {
  return `<svg class="ui-icon" viewBox="0 0 16 16" aria-hidden="true">${UI_ICONS[name] || ""}</svg>`;
}
