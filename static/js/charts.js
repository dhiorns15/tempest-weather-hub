// Shared helpers used by both the current-conditions trend chart (site.js)
// and the full history charts (history.js) — kept in one place so the two
// pages read /site/history rows the same way, and so the chart grid is
// generated from one config instead of duplicated across two HTML files.

const CHART_COLORS = {
  temperature: "#2f6fed",
  feelsLike: "#f2994a",
  dewPoint: "#56ccf2",
  wetBulb: "#6fcf97",
  wetBulbGlobe: "#27ae60",
  humidity: "#20a4a0",
  windAvg: "#8a63d2",
  windGust: "#d2638a",
  windLull: "#bb6bd9",
  pressure: "#c98a2e",
  seaLevelPressure: "#9b51e0",
  uv: "#e0574a",
  solar: "#e0a83c",
  brightness: "#f2c94c",
  precip: "#4a90e0",
  precipYesterday: "#2d9cdb",
  precipProbability: "#9b51e0",
  precipMinutesToday: "#2f80ed",
  precipMinutesYesterday: "#56ccf2",
  lightning1hr: "#eb5757",
  lightning3hr: "#f2994a",
  lightningDistance: "#9b51e0",
  airDensity: "#828282",
  deltaT: "#4f4f4f",
};

function fieldValue(row, resolution, rawField, aggField) {
  return resolution === "raw" ? row[rawField] : row[aggField];
}

function pointTimestampMs(row, resolution) {
  return resolution === "raw" ? row.ts * 1000 : Date.parse(row.bucket);
}

function formatChartLabel(ms, resolution) {
  const date = new Date(ms);
  if (resolution === "raw") {
    return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  if (resolution === "hourly") {
    return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric" });
  }
  return date.toLocaleDateString([], { month: "short", day: "numeric" });
}

function hexWithAlpha(hex, alpha) {
  return hex + alpha;
}

// Icon -> page-background class. Groups near-duplicate day/night variants
// that should look the same (e.g. all sleet variants share one gradient).
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

function applyWeatherBackground(icon) {
  const className = WEATHER_BG_CLASSES[icon] || DEFAULT_WEATHER_BG_CLASS;
  document.body.classList.remove(...new Set(Object.values(WEATHER_BG_CLASSES)));
  document.body.classList.add(className);
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

loadStationHeader();

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
}

// Which fields to chart, and where. Shared by history.js (full range picker)
// and site.js (fixed last-24h view) so both stay in sync automatically.
// `title` is the chart-section heading; `label` is the dataset legend/tooltip
// text (includes units).
const METRICS = [
  {
    key: "temperature",
    source: "both",
    canvasId: "chart-temperature",
    title: "Temperature",
    label: "Temperature (°)",
    raw: "air_temperature",
    agg: "air_temperature_avg",
    color: CHART_COLORS.temperature,
    yTickSuffix: "°",
  },
  {
    key: "feelsLike",
    source: "cloud",
    canvasId: "chart-feels-like",
    title: "Feels Like",
    label: "Feels Like (°)",
    raw: "feels_like",
    agg: "feels_like_avg",
    color: CHART_COLORS.feelsLike,
    yTickSuffix: "°",
  },
  {
    key: "dewPoint",
    source: "cloud",
    canvasId: "chart-dew-point",
    title: "Dew Point",
    label: "Dew Point (°)",
    raw: "dew_point",
    agg: "dew_point_avg",
    color: CHART_COLORS.dewPoint,
    yTickSuffix: "°",
  },
  {
    key: "wetBulb",
    source: "cloud",
    canvasId: "chart-wet-bulb",
    title: "Wet Bulb Temperature",
    label: "Wet Bulb (°)",
    raw: "wet_bulb_temperature",
    agg: "wet_bulb_temperature_avg",
    color: CHART_COLORS.wetBulb,
    yTickSuffix: "°",
  },
  {
    key: "wetBulbGlobe",
    source: "cloud",
    canvasId: "chart-wet-bulb-globe",
    title: "Wet Bulb Globe Temperature",
    label: "Wet Bulb Globe (°)",
    raw: "wet_bulb_globe_temperature",
    agg: "wet_bulb_globe_temperature_avg",
    color: CHART_COLORS.wetBulbGlobe,
    yTickSuffix: "°",
  },
  {
    key: "humidity",
    source: "both",
    canvasId: "chart-humidity",
    title: "Humidity",
    label: "Humidity (%)",
    raw: "relative_humidity",
    agg: "relative_humidity_avg",
    color: CHART_COLORS.humidity,
    yTickSuffix: "%",
  },
  {
    key: "pressure",
    source: "both",
    canvasId: "chart-pressure",
    title: "Station Pressure",
    label: "Pressure (inHg)",
    raw: "station_pressure",
    agg: "station_pressure_avg",
    color: CHART_COLORS.pressure,
  },
  {
    key: "seaLevelPressure",
    source: "cloud",
    canvasId: "chart-sea-level-pressure",
    title: "Sea Level Pressure",
    label: "Sea Level Pressure (inHg)",
    raw: "sea_level_pressure",
    agg: "sea_level_pressure_avg",
    color: CHART_COLORS.seaLevelPressure,
  },
  {
    key: "uv",
    source: "both",
    canvasId: "chart-uv",
    title: "UV Index",
    label: "UV Index",
    raw: "uv",
    agg: "uv_max",
    color: CHART_COLORS.uv,
  },
  {
    key: "solar",
    source: "both",
    canvasId: "chart-solar",
    title: "Solar Radiation",
    label: "Solar Radiation (W/m²)",
    raw: "solar_radiation",
    agg: "solar_radiation_avg",
    color: CHART_COLORS.solar,
  },
  {
    key: "brightness",
    source: "cloud",
    canvasId: "chart-brightness",
    title: "Brightness",
    label: "Brightness (lux)",
    raw: "brightness",
    agg: "brightness_avg",
    color: CHART_COLORS.brightness,
  },
  {
    key: "precip",
    source: "cloud",
    canvasId: "chart-precip",
    title: "Precipitation Today",
    label: "Precipitation Today (in)",
    raw: "precip_accum_local_day",
    agg: "precip_accum_local_day_max",
    color: CHART_COLORS.precip,
  },
  {
    key: "precipYesterday",
    source: "cloud",
    canvasId: "chart-precip-yesterday",
    title: "Precipitation Yesterday",
    label: "Precipitation Yesterday (in)",
    raw: "precip_accum_local_yesterday",
    agg: "precip_accum_local_yesterday_max",
    color: CHART_COLORS.precipYesterday,
  },
  {
    key: "precipProbability",
    source: "cloud",
    canvasId: "chart-precip-probability",
    title: "Precipitation Probability",
    label: "Precip. Probability (%)",
    raw: "precip_probability",
    agg: "precip_probability_avg",
    color: CHART_COLORS.precipProbability,
    yTickSuffix: "%",
  },
  {
    key: "precipMinutesToday",
    source: "cloud",
    canvasId: "chart-precip-minutes-today",
    title: "Rain Duration Today",
    label: "Rain Duration Today (min)",
    raw: "precip_minutes_local_day",
    agg: "precip_minutes_local_day_max",
    color: CHART_COLORS.precipMinutesToday,
  },
  {
    key: "precipMinutesYesterday",
    source: "cloud",
    canvasId: "chart-precip-minutes-yesterday",
    title: "Rain Duration Yesterday",
    label: "Rain Duration Yesterday (min)",
    raw: "precip_minutes_local_yesterday",
    agg: "precip_minutes_local_yesterday_max",
    color: CHART_COLORS.precipMinutesYesterday,
  },
  {
    key: "lightning1hr",
    source: "cloud",
    canvasId: "chart-lightning-1hr",
    title: "Lightning Strikes (last 1hr)",
    label: "Strikes (last 1hr)",
    raw: "lightning_strike_count_last_1hr",
    agg: "lightning_strike_count_last_1hr_max",
    color: CHART_COLORS.lightning1hr,
  },
  {
    key: "lightning3hr",
    source: "cloud",
    canvasId: "chart-lightning-3hr",
    title: "Lightning Strikes (last 3hr)",
    label: "Strikes (last 3hr)",
    raw: "lightning_strike_count_last_3hr",
    agg: "lightning_strike_count_last_3hr_max",
    color: CHART_COLORS.lightning3hr,
  },
  {
    key: "lightningDistance",
    source: "cloud",
    canvasId: "chart-lightning-distance",
    title: "Nearest Lightning Strike",
    label: "Distance (mi)",
    raw: "lightning_strike_last_distance",
    agg: "lightning_strike_last_distance_min",
    color: CHART_COLORS.lightningDistance,
  },
  {
    key: "airDensity",
    source: "cloud",
    canvasId: "chart-air-density",
    title: "Air Density",
    label: "Air Density (kg/m³)",
    raw: "air_density",
    agg: "air_density_avg",
    color: CHART_COLORS.airDensity,
  },
  {
    key: "deltaT",
    source: "cloud",
    canvasId: "chart-delta-t",
    title: "Delta T",
    label: "Delta T (°)",
    raw: "delta_t",
    agg: "delta_t_avg",
    color: CHART_COLORS.deltaT,
  },
];

const METRICS_BY_KEY = Object.fromEntries(METRICS.map((m) => [m.key, m]));

// Which charts appear in which order/grouping. "wind" is a pseudo-key
// handled specially (it's a 3-line chart, not a METRICS entry).
const CHART_LAYOUT = [
  {
    heading: "Temperature & Comfort",
    keys: ["temperature", "feelsLike", "dewPoint", "wetBulb", "wetBulbGlobe", "humidity"],
  },
  { heading: "Wind", keys: ["wind"] },
  {
    heading: "Pressure & Light",
    keys: ["pressure", "seaLevelPressure", "uv", "solar", "brightness"],
  },
  {
    heading: "Precipitation",
    keys: [
      "precip",
      "precipYesterday",
      "precipProbability",
      "precipMinutesToday",
      "precipMinutesYesterday",
    ],
  },
  { heading: "Lightning", keys: ["lightning1hr", "lightning3hr", "lightningDistance"] },
  { heading: "Advanced", keys: ["airDensity", "deltaT"] },
];

// Builds the chart-section DOM (once) inside the given empty container,
// following CHART_LAYOUT. Charts are populated separately by
// renderAllMetricCharts on each data refresh.
// A metric is available if its source is "both" (works off either REST or
// UDP data) or its required source is actually configured. "wind" is always
// shown since avg/gust come from whichever source is configured either way.
function isMetricAvailable(key) {
  if (key === "wind") return true;
  const metric = METRICS_BY_KEY[key];
  return metric.source !== "cloud" || CAPABILITIES.cloud_configured;
}

function buildChartGrid(containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;

  container.innerHTML = CHART_LAYOUT.map(({ heading, keys }) => {
    const visibleKeys = keys.filter(isMetricAvailable);
    if (visibleKeys.length === 0) return "";

    const sections = visibleKeys
      .map((key) => {
        if (key === "wind") {
          return `
            <section class="chart-section">
              <h3 class="section-title">Wind</h3>
              <div class="chart-wrap"><canvas id="chart-wind" height="220"></canvas></div>
            </section>
          `;
        }
        const metric = METRICS_BY_KEY[key];
        return `
          <section class="chart-section">
            <h3 class="section-title">${metric.title}</h3>
            <div class="chart-wrap"><canvas id="${metric.canvasId}" height="220"></canvas></div>
          </section>
        `;
      })
      .join("");

    return `<h2 class="chart-group-heading">${heading}</h2><div class="chart-group">${sections}</div>`;
  }).join("");
}

// chartsStore is a plain {} the caller owns (one per page) mapping metric key
// -> Chart.js instance, so repeated refreshes update in place instead of
// creating a new chart each time.
function renderMetricChart(chartsStore, metric, labels, rows, resolution) {
  const canvas = document.getElementById(metric.canvasId);
  if (!canvas) return;
  const values = rows.map((row) => fieldValue(row, resolution, metric.raw, metric.agg));

  chartsStore[metric.key] = renderLineChart(
    chartsStore[metric.key],
    canvas,
    labels,
    [
      {
        label: metric.label,
        data: values,
        borderColor: metric.color,
        backgroundColor: hexWithAlpha(metric.color, "22"),
        tension: 0.3,
        fill: true,
        pointRadius: 0,
      },
    ],
    metric.yTickSuffix
  );
}

function renderWindChart(chartsStore, labels, rows, resolution) {
  const canvas = document.getElementById("chart-wind");
  if (!canvas) return;
  const lull = rows.map((row) => fieldValue(row, resolution, "wind_lull", "wind_lull_avg"));
  const avg = rows.map((row) => fieldValue(row, resolution, "wind_avg", "wind_avg_avg"));
  const gust = rows.map((row) => fieldValue(row, resolution, "wind_gust", "wind_gust_max"));

  chartsStore.wind = renderLineChart(chartsStore.wind, canvas, labels, [
    {
      label: "Lull (mph)",
      data: lull,
      borderColor: CHART_COLORS.windLull,
      tension: 0.3,
      pointRadius: 0,
    },
    {
      label: "Avg (mph)",
      data: avg,
      borderColor: CHART_COLORS.windAvg,
      tension: 0.3,
      pointRadius: 0,
    },
    {
      label: "Gust (mph)",
      data: gust,
      borderColor: CHART_COLORS.windGust,
      tension: 0.3,
      pointRadius: 0,
    },
  ]);
}

// Renders every metric (all charts on the page) from one /site/history response.
function renderAllMetricCharts(chartsStore, rows, resolution) {
  const labels = rows.map((row) => formatChartLabel(pointTimestampMs(row, resolution), resolution));
  METRICS.forEach((metric) => renderMetricChart(chartsStore, metric, labels, rows, resolution));
  renderWindChart(chartsStore, labels, rows, resolution);
}

// Renders (or updates, if given an existing Chart instance) a line chart.
// Returns the Chart instance so the caller can reuse it on the next refresh.
function renderLineChart(existingChart, canvas, labels, datasets, yTickSuffix) {
  const data = { labels, datasets };

  if (existingChart) {
    existingChart.data = data;
    existingChart.update();
    return existingChart;
  }

  return new Chart(canvas, {
    type: "line",
    data,
    options: {
      responsive: true,
      plugins: { legend: { display: datasets.length > 1 } },
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: { maxTicksLimit: 8 } },
        y: yTickSuffix
          ? { ticks: { callback: (v) => `${v}${yTickSuffix}` } }
          : {},
      },
    },
  });
}
