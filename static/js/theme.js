// Colour theme: Auto (follow the OS, with night and storm skies always
// dark), Light or Dark, remembered per browser.
//
// Loaded as a plain blocking script in <head>, straight after site.css, so
// the theme is settled before the first paint - no flash of the wrong one.
//
// site.css writes its dark theme once, inside @media (prefers-color-scheme:
// dark). Without JavaScript that just follows the OS. This script re-points
// that media query instead of adding classes: "all" forces the dark theme,
// "not all" forces the light one, and Auto puts the OS query back (or uses
// "all" while the sky is a night/storm one). If this script never runs, the
// page still follows the OS setting.
(() => {
  const STORAGE_KEY = "weatherhub.theme";
  const SKY_STORAGE_KEY = "weatherhub.sky"; // the last sky, saved by charts.js
  const PREFERENCES = ["auto", "light", "dark"];
  const ALWAYS_DARK_SKIES = [
    "weather-clear-night",
    "weather-partly-cloudy-night",
    "weather-rainy-night",
    "weather-snow-night",
    "weather-thunderstorm",
  ];
  const OS_DARK_QUERY = "(prefers-color-scheme: dark)";
  const osDark = window.matchMedia(OS_DARK_QUERY);
  const root = document.documentElement;

  let darkRules = [];

  // Every top-level dark-scheme media rule in the page's own stylesheets.
  // Collected once, before anything is re-pointed (after that their media
  // text no longer says prefers-color-scheme).
  function findDarkRules() {
    if (darkRules.length) return darkRules;
    const wanted = OS_DARK_QUERY.replace(/\s+/g, "");
    for (const sheet of document.styleSheets) {
      let rules;
      try {
        rules = sheet.cssRules;
      } catch (err) {
        continue; // not readable (cross-origin) - not ours anyway
      }
      for (const rule of rules) {
        if (rule.media && rule.media.mediaText.replace(/\s+/g, "").toLowerCase() === wanted) {
          darkRules.push(rule);
        }
      }
    }
    return darkRules;
  }

  function readPreference() {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return PREFERENCES.includes(stored) ? stored : "auto";
    } catch (err) {
      return "auto"; // storage unavailable - Auto, which needs nothing stored
    }
  }

  let preference = readPreference();

  // Before charts.js has run there's no sky class yet, so use the sky it
  // remembered from last time (it paints that one straight away too).
  function skyIsAlwaysDark() {
    const body = document.body;
    if (body && /\bweather-/.test(body.className)) {
      return ALWAYS_DARK_SKIES.some((name) => body.classList.contains(name));
    }
    try {
      return ALWAYS_DARK_SKIES.includes(localStorage.getItem(SKY_STORAGE_KEY));
    } catch (err) {
      return false;
    }
  }

  // The <meta name="theme-color"> pair (light/dark) colours the browser's
  // own toolbar. A forced theme points both at that theme's colour.
  const themeColorMetas = Array.from(document.querySelectorAll('meta[name="theme-color"]'));
  const themeColors = {};
  themeColorMetas.forEach((meta) => {
    meta.dataset.osContent = meta.content;
    themeColors[/dark/.test(meta.media) ? "dark" : "light"] = meta.content;
  });

  function syncControls() {
    document.querySelectorAll('.theme-toggle input[name="theme"]').forEach((input) => {
      input.checked = input.value === preference;
    });
  }

  let applied = null;

  function apply() {
    let forced = null;
    if (preference !== "auto") forced = preference;
    else if (skyIsAlwaysDark()) forced = "dark";

    let mediaText = OS_DARK_QUERY;
    if (forced === "dark") mediaText = "all";
    else if (forced === "light") mediaText = "not all";

    findDarkRules().forEach((rule) => {
      if (rule.media.mediaText !== mediaText) rule.media.mediaText = mediaText;
    });

    const effective = forced || (osDark.matches ? "dark" : "light");
    root.dataset.themePref = preference;
    root.dataset.theme = effective;

    themeColorMetas.forEach((meta) => {
      meta.content = (forced && themeColors[forced]) || meta.dataset.osContent;
    });

    syncControls();

    // Charts are drawn on a canvas, so they don't pick up the CSS change by
    // themselves. (refreshChartTheme is charts.js's; not loaded yet on the
    // first run in <head>, when there are no charts to repaint anyway.)
    const state = `${mediaText}|${effective}`;
    if (state !== applied) {
      applied = state;
      if (typeof window.refreshChartTheme === "function") window.refreshChartTheme();
    }
  }

  function setPreference(value) {
    if (!PREFERENCES.includes(value)) return;
    preference = value;
    try {
      if (value === "auto") localStorage.removeItem(STORAGE_KEY);
      else localStorage.setItem(STORAGE_KEY, value);
    } catch (err) {
      // not remembered next time, but still applied now
    }
    apply();
  }

  apply();

  // The OS setting only matters in Auto, but data-theme should stay true.
  osDark.addEventListener("change", apply);

  // Night/storm skies go dark in Auto: follow charts.js changing the sky.
  // (It refreshes the charts itself, but before this has re-pointed the
  // media query - apply() repaints them again once it has.)
  new MutationObserver((records) => {
    if (records.some((record) => record.target === document.body)) apply();
  }).observe(root, { attributes: true, attributeFilter: ["class"], subtree: true });

  // Another tab changed the choice.
  window.addEventListener("storage", (event) => {
    if (event.key === STORAGE_KEY || event.key === null) {
      preference = readPreference();
      apply();
    }
  });

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll('.theme-toggle input[name="theme"]').forEach((input) => {
      input.addEventListener("change", () => {
        if (input.checked) setPreference(input.value);
      });
    });
    apply();
  });
})();
