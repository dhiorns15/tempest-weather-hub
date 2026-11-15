// Shared by both pages: the weather-driven page background, the capability
// lookup, and every history chart. Kept in one place so the current page
// (site.js, fixed last-24h view) and the history page (history.js, range
// picker) read /site/history rows the same way and build the same chart grid.

// ---------------------------------------------------------------------------
// Weather-driven page background
// ---------------------------------------------------------------------------

// Icon -> page-background class. Groups near-duplicate day/night variants
// that should look the same (e.g. all sleet variants share one sky).
const WEATHER_BG_CLASSES = {
  "clear-day": "weather-clear-day",
  "clear-night": "weather-clear-night",
  cloudy: "weather-cloudy",
  "partly-cloudy-day": "weather-partly-cloudy-day",
  "partly-cloudy-night": "weather-partly-cloudy-night",
  foggy: "weather-foggy",
  "possibly-rainy-day": "weather-rainy",
  "possibly-rainy-night": "weather-rainy-night",
  rainy: "weather-rainy",
  "possibly-sleet-day": "weather-sleet",
  "possibly-sleet-night": "weather-sleet",
  sleet: "weather-sleet",
  "possibly-snow-day": "weather-snow-day",
  "possibly-snow-night": "weather-snow-night",
  snow: "weather-snow-day",
  "possibly-thunderstorm-day": "weather-thunderstorm",
  "possibly-thunderstorm-night": "weather-thunderstorm",
  thunderstorm: "weather-thunderstorm",
  windy: "weather-windy",
};
const DEFAULT_WEATHER_BG_CLASS = "weather-clear-day";
const ALL_WEATHER_BG_CLASSES = [...new Set(Object.values(WEATHER_BG_CLASSES))];
const SKY_STORAGE_KEY = "weatherhub.sky";

function setWeatherClass(className) {
  if (document.body.classList.contains(className)) return;
  document.body.classList.remove(...ALL_WEATHER_BG_CLASSES);
  document.body.classList.add(className);
  // Night/storm skies switch the whole page to the dark tokens, so the
  // charts' colours have to follow.
  refreshChartTheme();
}

function applyWeatherBackground(icon) {
  const className = WEATHER_BG_CLASSES[icon] || DEFAULT_WEATHER_BG_CLASS;
  setWeatherClass(className);
  try {
    localStorage.setItem(SKY_STORAGE_KEY, className);
  } catch (err) {
    // storage unavailable (private window etc.) - purely a nicety, skip it
  }
}

// Shared across both pages: populates #station-name (in the header) and the
// weather-condition page background from /api/current, on initial load.
// site.js additionally re-applies the background on every refresh for the
// current page; history.html only gets it once per page load, which is fine
// since you're not staring at it waiting for the sky to change.
async function loadStationHeader() {
  try {
    const response = await fetch("/api/current");
    if (!response.ok) return;
    const data = await response.json();

    const nameEl = document.getElementById("station-name");
    if (nameEl && data.location_name) nameEl.textContent = data.location_name;

    applyWeatherBackground(data.icon);
  } catch (err) {
    // leave header/background as-is; not worth surfacing an error for this
  }
}

// Which data source(s) are configured server-side. Populated once at page
// load via loadCapabilities(); defaults assume everything's available so a
// slow/failed fetch doesn't hide content that's actually there.
let CAPABILITIES = { cloud_configured: true, udp_configured: true };

async function loadCapabilities() {
  try {
    const response = await fetch("/api/capabilities");
    if (response.ok) CAPABILITIES = await response.json();
  } catch (err) {
    // keep the permissive defaults above
  }
  if (CAPABILITIES.units) setUnits(CAPABILITIES.units);
}

// Display labels for the units the API's numbers are in (TEMPEST_UNITS on
// the server), from /api/capabilities. Imperial - the server's default -
// until that arrives, or if it fails.
let UNITS = {
  system: "imperial",
  temp: "°F",
  wind: "mph",
  pressure: "inHg",
  precip: "in",
  distance: "mi",
};

// Decimal places for a current-conditions reading, per unit system: whole
// mph is plenty but whole m/s is too coarse, and inHg needs two places where
// mb needs none.
const READING_DECIMALS = {
  imperial: { wind: 0, pressure: 2, precip: 2 },
  metric: { wind: 1, pressure: 0, precip: 1 },
};

function setUnits(units) {
  UNITS = { ...UNITS, ...units };
  applyChartUnits();
}

function formatReading(value, quantity) {
  const decimals = (READING_DECIMALS[UNITS.system] || READING_DECIMALS.imperial)[quantity];
  return value.toFixed(decimals);
}

// ---------------------------------------------------------------------------
// Chart theme - read from the CSS custom properties so charts always match
// whatever theme the page is actually showing.
// ---------------------------------------------------------------------------

function readChartTheme() {
  const styles = getComputedStyle(document.body);
  const value = (name) => styles.getPropertyValue(name).trim();
  return {
    series: [value("--series-1"), value("--series-2"), value("--series-3")],
    text: value("--text"),
    tick: value("--chart-tick"),
    grid: value("--chart-grid"),
    crosshair: value("--chart-crosshair"),
    tooltipBg: value("--tooltip-bg"),
    tooltipBorder: value("--tooltip-border"),
  };
}

let CHART_THEME = readChartTheme();

function hexToRgba(hex, alpha) {
  const n = parseInt(hex.replace("#", ""), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

function applyChartTheme(chart) {
  const theme = CHART_THEME;
  const { scales, plugins } = chart.options;

  scales.x.ticks.color = theme.tick;
  scales.y.ticks.color = theme.tick;
  scales.x.border.color = theme.grid;
  scales.y.grid.color = theme.grid;
  plugins.legend.labels.color = theme.tick;
  plugins.tooltip.backgroundColor = theme.tooltipBg;
  plugins.tooltip.borderColor = theme.tooltipBorder;
  plugins.tooltip.titleColor = theme.text;
  plugins.tooltip.bodyColor = theme.text;

  const single = chart.data.datasets.length === 1;
  chart.data.datasets.forEach((dataset) => {
    const color = theme.series[dataset.seriesSlot];
    dataset.borderColor = color;
    dataset.pointHoverBackgroundColor = color;
    dataset.pointHoverBorderColor = theme.tooltipBg;
    // Area wash only where there's a single series; overlapping washes
    // from several series just turn to mud.
    dataset.backgroundColor = single ? hexToRgba(color, 0.1) : color;
  });
}

function refreshChartTheme() {
  CHART_THEME = readChartTheme();
  if (typeof Chart === "undefined") return;
  Object.values(Chart.instances).forEach((chart) => {
    applyChartTheme(chart);
    chart.update("none");
  });
}

window
  .matchMedia("(prefers-color-scheme: dark)")
  .addEventListener("change", () => refreshChartTheme());

// Thin vertical guide at the hovered point, so it's clear which instant the
// tooltip is describing.
const crosshairPlugin = {
  id: "crosshair",
  afterDatasetsDraw(chart) {
    const active = chart.tooltip && chart.tooltip.getActiveElements();
    if (!active || !active.length) return;
    const x = active[0].element.x;
    const { top, bottom } = chart.chartArea;
    const { ctx } = chart;
    ctx.save();
    ctx.strokeStyle = CHART_THEME.crosshair;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.stroke();
    ctx.restore();
  },
};

// ---------------------------------------------------------------------------
// Chart definitions
// ---------------------------------------------------------------------------

// One entry per chart card. `series[].raw` is the column at raw resolution,
// `agg` the column at hourly/daily resolution. `source` is which data source
// a series needs: "both" works off either, "cloud"/"udp" only that one.
// Series take the theme's colour slots in the order listed - at most three
// per chart, which is as far as the palette stays distinguishable for
// colour-blind readers.
//
// minSpan: the smallest y-range the chart will zoom in to, so a value that
//   barely moved doesn't get stretched into a dramatic-looking swing.
// floor/ceil: hard limits the padded y-range never crosses.
// quantity: for a value in a unit-system-dependent unit (temp, wind,
//   pressure, precip, distance), which UNITS label it takes - in place of a
//   fixed `unit`. Its decimals/minSpan are for imperial; `metric` overrides
//   them (see applyChartUnits).
// grouping: false to print 1014 rather than 1,014 (pressure in mb).
const CHARTS = [
  {
    key: "temperature",
    title: "Temperature",
    quantity: "temp",
    tickSuffix: "°",
    decimals: 1,
    minSpan: 4,
    metric: { minSpan: 2 },
    series: [
      { label: "Temperature", raw: "air_temperature", agg: "air_temperature_avg", source: "both" },
      { label: "Feels like", raw: "feels_like", agg: "feels_like_avg", source: "cloud" },
      { label: "Dew point", raw: "dew_point", agg: "dew_point_avg", source: "cloud" },
    ],
  },
  {
    key: "humidity",
    title: "Humidity",
    unit: "%",
    tickSuffix: "%",
    decimals: 0,
    minSpan: 10,
    floor: 0,
    ceil: 100,
    series: [
      { label: "Humidity", raw: "relative_humidity", agg: "relative_humidity_avg", source: "both" },
    ],
  },
  {
    key: "wind",
    title: "Wind",
    quantity: "wind",
    decimals: 1,
    minSpan: 5,
    metric: { minSpan: 2 },
    floor: 0,
    series: [
      { label: "Average", raw: "wind_avg", agg: "wind_avg_avg", source: "both" },
      { label: "Gust", raw: "wind_gust", agg: "wind_gust_max", source: "both" },
      { label: "Lull", raw: "wind_lull", agg: "wind_lull_avg", source: "udp" },
    ],
  },
  {
    key: "pressure",
    title: "Station Pressure",
    quantity: "pressure",
    decimals: 2,
    minSpan: 0.1,
    metric: { decimals: 0, minSpan: 3, grouping: false },
    series: [
      { label: "Station pressure", raw: "station_pressure", agg: "station_pressure_avg", source: "both" },
    ],
  },
  {
    key: "uv",
    title: "UV Index",
    unit: "",
    decimals: 1,
    minSpan: 2,
    floor: 0,
    series: [{ label: "UV index", raw: "uv", agg: "uv_max", source: "both" }],
  },
  {
    key: "solar",
    title: "Solar Radiation",
    unit: " W/m²",
    decimals: 0,
    minSpan: 100,
    floor: 0,
    series: [
      { label: "Solar radiation", raw: "solar_radiation", agg: "solar_radiation_avg", source: "both" },
    ],
  },
  {
    key: "precip",
    title: "Rain Accumulation",
    quantity: "precip",
    decimals: 2,
    minSpan: 0.1,
    metric: { decimals: 1, minSpan: 2.5 },
    floor: 0,
    series: [
      { label: "Today", raw: "precip_accum_local_day", agg: "precip_accum_local_day_max", source: "cloud" },
      {
        label: "Yesterday",
        raw: "precip_accum_local_yesterday",
        agg: "precip_accum_local_yesterday_max",
        source: "cloud",
      },
    ],
  },
  {
    key: "precipProbability",
    title: "Chance of Rain",
    unit: "%",
    tickSuffix: "%",
    decimals: 0,
    minSpan: 20,
    floor: 0,
    ceil: 100,
    series: [
      { label: "Chance of rain", raw: "precip_probability", agg: "precip_probability_avg", source: "cloud" },
    ],
  },
  {
    key: "lightningStrikes",
    title: "Lightning Strikes",
    unit: "",
    decimals: 0,
    minSpan: 4,
    floor: 0,
    series: [
      {
        label: "Last hour",
        raw: "lightning_strike_count_last_1hr",
        agg: "lightning_strike_count_last_1hr_max",
        source: "cloud",
      },
      {
        label: "Last 3 hours",
        raw: "lightning_strike_count_last_3hr",
        agg: "lightning_strike_count_last_3hr_max",
        source: "cloud",
      },
    ],
  },
  {
    key: "lightningDistance",
    title: "Nearest Lightning Strike",
    quantity: "distance",
    decimals: 0,
    minSpan: 10,
    metric: { minSpan: 15 },
    floor: 0,
    series: [
      {
        label: "Distance",
        raw: "lightning_strike_last_distance",
        agg: "lightning_strike_last_distance_min",
        source: "cloud",
      },
    ],
  },
  {
    key: "seaLevelPressure",
    title: "Sea Level Pressure",
    quantity: "pressure",
    decimals: 2,
    minSpan: 0.1,
    metric: { decimals: 0, minSpan: 3, grouping: false },
    series: [
      { label: "Sea level pressure", raw: "sea_level_pressure", agg: "sea_level_pressure_avg", source: "cloud" },
    ],
  },
  {
    key: "brightness",
    title: "Brightness",
    unit: " lux",
    decimals: 0,
    minSpan: 1000,
    floor: 0,
    compactTicks: true,
    series: [{ label: "Brightness", raw: "brightness", agg: "brightness_avg", source: "cloud" }],
  },
  {
    key: "rainDuration",
    title: "Rain Duration",
    unit: " min",
    decimals: 0,
    minSpan: 10,
    floor: 0,
    series: [
      { label: "Today", raw: "precip_minutes_local_day", agg: "precip_minutes_local_day_max", source: "cloud" },
      {
        label: "Yesterday",
        raw: "precip_minutes_local_yesterday",
        agg: "precip_minutes_local_yesterday_max",
        source: "cloud",
      },
    ],
  },
  {
    key: "wetBulb",
    title: "Wet Bulb Temperature",
    quantity: "temp",
    tickSuffix: "°",
    decimals: 1,
    minSpan: 4,
    metric: { minSpan: 2 },
    series: [
      { label: "Wet bulb", raw: "wet_bulb_temperature", agg: "wet_bulb_temperature_avg", source: "cloud" },
      {
        label: "Wet bulb globe",
        raw: "wet_bulb_globe_temperature",
        agg: "wet_bulb_globe_temperature_avg",
        source: "cloud",
      },
    ],
  },
  {
    key: "airDensity",
    title: "Air Density",
    unit: " kg/m³",
    decimals: 2,
    minSpan: 0.05,
    series: [{ label: "Air density", raw: "air_density", agg: "air_density_avg", source: "cloud" }],
  },
  {
    key: "deltaT",
    title: "Delta T",
    quantity: "temp",
    tickSuffix: "°",
    decimals: 1,
    minSpan: 2,
    metric: { minSpan: 1 },
    series: [{ label: "Delta T", raw: "delta_t", agg: "delta_t_avg", source: "cloud" }],
  },
];

const CHARTS_BY_KEY = Object.fromEntries(CHARTS.map((def) => [def.key, def]));

// Fills in `unit` and the tuning for the current UNITS on every chart with
// a quantity. Safe to run again: the imperial values of whatever `metric`
// overrides are kept aside the first time, so switching back restores them.
function applyChartUnits() {
  CHARTS.forEach((def) => {
    if (!def.quantity) return;
    def.imperial =
      def.imperial || Object.fromEntries(Object.keys(def.metric).map((key) => [key, def[key]]));
    Object.assign(def, UNITS.system === "metric" ? def.metric : def.imperial);
    // "21.4°C", but "12 mph".
    def.unit = def.quantity === "temp" ? UNITS.temp : ` ${UNITS[def.quantity]}`;
  });
}

applyChartUnits();

// Which charts appear where. The first group is always visible; the rest
// are collapsed by default (open/closed is remembered per browser).
const CHART_LAYOUT = [
  {
    id: "primary",
    keys: [
      "temperature",
      "humidity",
      "wind",
      "pressure",
      "uv",
      "solar",
      "precip",
      "precipProbability",
    ],
  },
  { id: "lightning", heading: "Lightning", keys: ["lightningStrikes", "lightningDistance"] },
  {
    id: "advanced",
    heading: "Advanced",
    keys: ["seaLevelPressure", "brightness", "rainDuration", "wetBulb", "airDensity", "deltaT"],
  },
];

function isSourceAvailable(source) {
  if (source === "cloud") return CAPABILITIES.cloud_configured;
  if (source === "udp") return CAPABILITIES.udp_configured;
  return true;
}

function availableSeries(def) {
  return def.series.filter((series) => isSourceAvailable(series.source));
}

function groupStorageKey(id) {
  return `weatherhub.charts.${id}.open`;
}

// Builds the chart-card DOM (once) inside the given empty container,
// following CHART_LAYOUT. Charts are populated separately by
// renderAllMetricCharts on each data refresh. A chart is left out entirely
// when none of its series can exist with the configured data source(s).
function buildChartGrid(containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;

  container.innerHTML = CHART_LAYOUT.map(({ id, heading, keys }) => {
    const cards = keys
      .map((key) => CHARTS_BY_KEY[key])
      .filter((def) => availableSeries(def).length > 0)
      .map(
        (def) => `
          <section class="card chart-card">
            <div class="chart-head">
              <h3>${def.title}</h3>
              <span class="chart-latest" id="chart-latest-${def.key}"></span>
            </div>
            <div class="chart-wrap"><canvas id="chart-${def.key}"></canvas></div>
          </section>`
      );
    if (cards.length === 0) return "";

    const grid = `<div class="chart-grid">${cards.join("")}</div>`;
    if (!heading) return grid;

    let open = false;
    try {
      open = localStorage.getItem(groupStorageKey(id)) === "1";
    } catch (err) {
      // storage unavailable - just start collapsed
    }
    return `
      <details class="chart-group" data-group="${id}"${open ? " open" : ""}>
        <summary>${heading} <span class="group-count">${cards.length} charts</span></summary>
        ${grid}
      </details>`;
  }).join("");

  container.querySelectorAll("details.chart-group").forEach((details) => {
    details.addEventListener("toggle", () => {
      try {
        localStorage.setItem(groupStorageKey(details.dataset.group), details.open ? "1" : "0");
      } catch (err) {
        // not remembered this time; harmless
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Time axis
//
// Points are placed by their real timestamp (a linear axis in ms), not
// evenly by index, so an outage shows up as a gap instead of being silently
// closed up. Chart.js's own time scale needs a date-adapter library; picking
// the tick positions by hand keeps this dependency-free.
// ---------------------------------------------------------------------------

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

const TIME_STEPS = [
  5 * MINUTE_MS,
  15 * MINUTE_MS,
  30 * MINUTE_MS,
  HOUR_MS,
  2 * HOUR_MS,
  3 * HOUR_MS,
  6 * HOUR_MS,
  12 * HOUR_MS,
  DAY_MS,
  2 * DAY_MS,
  7 * DAY_MS,
  14 * DAY_MS,
  30 * DAY_MS,
  60 * DAY_MS,
  90 * DAY_MS,
];

// A line is only drawn between two readings up to this far apart. Further
// than that is treated as a real gap (station/service was down). Raw allows
// for cloud-only fields arriving every few minutes between UDP rows.
const MAX_GAP_MS = {
  raw: 45 * MINUTE_MS,
  hourly: 3 * HOUR_MS,
  daily: 3 * DAY_MS,
};

function fieldValue(row, resolution, rawField, aggField) {
  return resolution === "raw" ? row[rawField] : row[aggField];
}

function pointTimestampMs(row, resolution) {
  return resolution === "raw" ? row.ts * 1000 : Date.parse(row.bucket);
}

// Daily buckets are the station's local calendar days, sent as that date at
// UTC midnight ("2026-11-14T00:00:00Z"), so they're labelled with their UTC
// date - converting to local time would show every bucket as the day before
// for anyone west of Greenwich.
function buildTimeTicks(min, max, maxTicks, utc) {
  const span = max - min;
  const step = TIME_STEPS.find((s) => span / s <= maxTicks) || TIME_STEPS[TIME_STEPS.length - 1];
  const ticks = [];

  if (step < DAY_MS) {
    const offset = utc ? 0 : new Date(min).getTimezoneOffset() * MINUTE_MS;
    for (let t = Math.ceil((min - offset) / step) * step + offset; t <= max; t += step) {
      ticks.push({ value: t });
    }
  } else {
    const days = step / DAY_MS;
    const cursor = new Date(min);
    if (utc) cursor.setUTCHours(0, 0, 0, 0);
    else cursor.setHours(0, 0, 0, 0);
    const advance = (n) =>
      utc ? cursor.setUTCDate(cursor.getUTCDate() + n) : cursor.setDate(cursor.getDate() + n);
    while (cursor.getTime() < min) advance(1);
    for (; cursor.getTime() <= max; advance(days)) ticks.push({ value: cursor.getTime() });
  }

  return { step, ticks };
}

function formatTimeTick(ms, step, utc) {
  const date = new Date(ms);
  const timeZone = utc ? "UTC" : undefined;
  if (step >= DAY_MS) {
    return date.toLocaleDateString([], { month: "short", day: "numeric", timeZone });
  }
  if (date.getHours() === 0 && date.getMinutes() === 0) {
    return date.toLocaleDateString([], { month: "short", day: "numeric" });
  }
  return step >= HOUR_MS
    ? date.toLocaleTimeString([], { hour: "numeric" })
    : date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function formatTooltipTime(ms, resolution) {
  const date = new Date(ms);
  if (resolution === "daily") {
    return date.toLocaleDateString([], {
      weekday: "short",
      month: "short",
      day: "numeric",
      year: "numeric",
      timeZone: "UTC",
    });
  }
  return date.toLocaleString([], {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: resolution === "raw" ? "2-digit" : undefined,
  });
}

function formatChartValue(value, def) {
  if (value == null) return "—";
  const text = value.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: def.decimals,
    useGrouping: def.grouping !== false,
  });
  return `${text}${def.unit}`;
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function buildChartOptions(def) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: "nearestPerSeries", intersect: false },
    plugins: {
      // A single series is already named by the card title.
      legend: {
        display: false,
        position: "bottom",
        align: "start",
        labels: { usePointStyle: true, pointStyle: "line", boxWidth: 18, padding: 12, font: { size: 12 } },
      },
      tooltip: {
        borderWidth: 1,
        padding: 10,
        cornerRadius: 10,
        boxPadding: 5,
        usePointStyle: true,
        titleFont: { size: 12, weight: "600" },
        bodyFont: { size: 12 },
        callbacks: {
          title: (items) =>
            items.length ? formatTooltipTime(items[0].parsed.x, items[0].chart.$resolution) : "",
          label: (item) => ` ${item.dataset.label}: ${formatChartValue(item.parsed.y, def)}`,
          labelPointStyle: () => ({ pointStyle: "line", rotation: 0 }),
        },
      },
    },
    scales: {
      x: {
        type: "linear",
        grid: { display: false },
        border: {},
        ticks: {
          maxRotation: 0,
          autoSkip: false,
          font: { size: 11 },
          callback(value) {
            return formatTimeTick(value, this.$step, this.chart.$resolution === "daily");
          },
        },
        afterBuildTicks(scale) {
          const maxTicks = Math.max(2, Math.floor(scale.width / 70));
          const { step, ticks } = buildTimeTicks(
            scale.min,
            scale.max,
            maxTicks,
            scale.chart.$resolution === "daily"
          );
          scale.$step = step;
          scale.ticks = ticks;
        },
      },
      y: {
        grid: {},
        border: { display: false },
        ticks: {
          maxTicksLimit: 5,
          font: { size: 11 },
          format: def.compactTicks
            ? { notation: "compact" }
            : def.grouping === false
              ? { useGrouping: false }
              : undefined,
          callback(value, index, ticks) {
            const text = Chart.Ticks.formatters.numeric.call(this, value, index, ticks);
            return def.tickSuffix ? `${text}${def.tickSuffix}` : text;
          },
        },
        // Pad the range, and never zoom in further than minSpan - otherwise
        // a reading that moved by a rounding error fills the whole chart.
        afterDataLimits(scale) {
          let { min, max } = scale;
          if (!Number.isFinite(min) || !Number.isFinite(max)) return;
          const span = Math.max(max - min, def.minSpan);
          const mid = (min + max) / 2;
          const pad = span * 0.08;
          min = Math.min(min, mid - span / 2) - pad;
          max = Math.max(max, mid + span / 2) + pad;
          // Slide the window rather than squash it when it hits a limit.
          if (def.floor != null && min < def.floor) {
            max += def.floor - min;
            min = def.floor;
          }
          if (def.ceil != null && max > def.ceil) {
            min -= max - def.ceil;
            max = def.ceil;
            if (def.floor != null) min = Math.max(min, def.floor);
          }
          scale.min = min;
          scale.max = max;
        },
      },
    },
  };
}

// The cloud reports the same sensors as the local UDP broadcast, but rounded
// coarser (whole degrees, 0.1 inHg, integer UV), so at raw resolution its
// rows interleaved with the minute-by-minute UDP rows make a line zigzag.
// A cloud row's reading of a "both" series is left out when a UDP reading
// of that series lies within this distance of it either side; with no UDP
// reading that close (cloud-only setups, history from before UDP was
// enabled, UDP outages) cloud readings are all there is, so they stay.
const COARSE_READING_REACH_MS = 5 * MINUTE_MS;

// UDP rows always carry wind_lull, which the cloud doesn't report. (Cloud
// rows stored while a fresh UDP reading existed carry the UDP values,
// wind_lull included, so they count as local readings too.)
function isLocalReading(row) {
  return row.wind_lull != null;
}

// One series' real readings as {x: ms, y} points, oldest first.
function seriesPoints(rows, timestamps, resolution, series) {
  const skipCoarse = resolution === "raw" && series.source === "both";
  const localTimes = [];
  if (skipCoarse) {
    rows.forEach((row, i) => {
      if (isLocalReading(row) && row[series.raw] != null) localTimes.push(timestamps[i]);
    });
  }

  const points = [];
  let nextLocal = 0; // index of the first local reading at or after the current row
  rows.forEach((row, i) => {
    const y = fieldValue(row, resolution, series.raw, series.agg);
    if (y == null) return;
    const x = timestamps[i];
    if (localTimes.length && !isLocalReading(row)) {
      while (nextLocal < localTimes.length && localTimes[nextLocal] < x) nextLocal++;
      const before = nextLocal > 0 ? x - localTimes[nextLocal - 1] : Infinity;
      const after = nextLocal < localTimes.length ? localTimes[nextLocal] - x : Infinity;
      if (Math.min(before, after) <= COARSE_READING_REACH_MS) return;
    }
    points.push({ x, y });
  });
  return points;
}

// Index of the point closest in time to `x`, in points sorted by x.
function nearestPointIndex(points, x) {
  let lo = 0;
  let hi = points.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid].x < x) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && x - points[lo - 1].x <= points[lo].x - x) return lo - 1;
  return lo;
}

// Series in one chart have readings at different times (see seriesPoints),
// so the built-in "index" mode would pair up unrelated instants. This picks,
// for the hovered time, each series' closest reading - unless the pointer is
// in a hole in that series' data, where there is nothing honest to show.
function nearestPerSeries(chart, event) {
  const position = Chart.helpers.getRelativePosition(event, chart);
  const area = chart.chartArea;
  const inArea =
    position.x >= area.left &&
    position.x <= area.right &&
    position.y >= area.top &&
    position.y <= area.bottom;
  if (!inArea) return [];

  const x = chart.scales.x.getValueForPixel(position.x);
  const reach = MAX_GAP_MS[chart.$resolution] / 2;
  const items = [];
  chart.getSortedVisibleDatasetMetas().forEach((meta) => {
    const points = chart.data.datasets[meta.index].data;
    if (!points.length) return;
    const index = nearestPointIndex(points, x);
    const element = meta.data[index];
    if (element && Math.abs(points[index].x - x) <= reach) {
      items.push({ element, datasetIndex: meta.index, index });
    }
  });
  return items;
}

// chartsStore is a plain {} the caller owns (one per page) mapping chart key
// -> Chart.js instance, so repeated refreshes update in place instead of
// creating a new chart each time.
function renderChart(chartsStore, def, timestamps, rows, resolution) {
  const canvas = document.getElementById(`chart-${def.key}`);
  if (!canvas) return;

  const series = availableSeries(def);
  const datasets = series.map((s, slot) => ({
    label: s.label,
    seriesSlot: slot,
    // Cloud-only fields are null on UDP-sourced rows (and vice versa for
    // wind_lull), so at raw resolution most rows have no value for a given
    // series. Keep only the rows that do: Chart.js breaks a line at every
    // null point, and a numeric spanGaps only bridges the distance between
    // neighbouring points - it does not skip over nulls sitting between them.
    data: seriesPoints(rows, timestamps, resolution, s),
    // Join readings up to this far apart; leave a longer hole as a gap.
    spanGaps: MAX_GAP_MS[resolution],
    borderWidth: 2,
    borderJoinStyle: "round",
    cubicInterpolationMode: "monotone",
    fill: series.length === 1 ? "start" : false,
    pointRadius: 0,
    pointHoverRadius: 5,
    pointHoverBorderWidth: 2,
  }));

  const latestEl = document.getElementById(`chart-latest-${def.key}`);
  if (latestEl) {
    const lead = datasets[0].data;
    latestEl.textContent = lead.length ? formatChartValue(lead[lead.length - 1].y, def) : "";
  }

  let chart = chartsStore[def.key];
  if (!chart) {
    chart = new Chart(canvas, {
      type: "line",
      data: { datasets },
      options: buildChartOptions(def),
      plugins: [crosshairPlugin],
    });
    chartsStore[def.key] = chart;
  } else {
    chart.data.datasets = datasets;
  }

  chart.$resolution = resolution;
  chart.options.plugins.legend.display = datasets.length > 1;

  // Span exactly the data that exists; with a single point (or none) fall
  // back to Chart.js's own range so the axis doesn't collapse to zero width.
  const first = timestamps[0];
  const last = timestamps[timestamps.length - 1];
  const hasRange = timestamps.length > 1 && last > first;
  chart.options.scales.x.min = hasRange ? first : undefined;
  chart.options.scales.x.max = hasRange ? last : undefined;

  applyChartTheme(chart);
  chart.update("none");
}

// Renders every chart on the page from one /site/history response.
function renderAllMetricCharts(chartsStore, rows, resolution) {
  CHART_THEME = readChartTheme();
  const timestamps = rows.map((row) => pointTimestampMs(row, resolution));
  CHARTS.forEach((def) => renderChart(chartsStore, def, timestamps, rows, resolution));
}

if (typeof Chart !== "undefined") {
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.Interaction.modes.nearestPerSeries = nearestPerSeries;
}

// Paint the last-seen sky straight away so the page doesn't flash the
// neutral background while /api/current is in flight.
try {
  const remembered = localStorage.getItem(SKY_STORAGE_KEY);
  if (ALL_WEATHER_BG_CLASSES.includes(remembered)) setWeatherClass(remembered);
} catch (err) {
  // storage unavailable - the fetch below sets it shortly anyway
}

loadStationHeader();
