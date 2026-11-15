const RANGES = {
  day: { seconds: 86400, resolution: "raw", label: "Last 24 hours" },
  week: { seconds: 7 * 86400, resolution: "hourly", label: "Last 7 days" },
  month: { seconds: 30 * 86400, resolution: "hourly", label: "Last 30 days" },
  year: { seconds: 365 * 86400, resolution: "daily", label: "Last 12 months" },
};

const RESOLUTION_NOTES = {
  raw: "every observation",
  hourly: "hourly averages",
  daily: "daily averages",
};

const charts = {}; // chart key -> Chart.js instance, reused across range switches
let currentRange = null; // { start, end, resolution } for whatever's on screen, used by export buttons

function renderStats(rows, resolution) {
  const values = rows.map((row) => ({
    avg: fieldValue(row, resolution, "air_temperature", "air_temperature_avg"),
    min: fieldValue(row, resolution, "air_temperature", "air_temperature_min"),
    max: fieldValue(row, resolution, "air_temperature", "air_temperature_max"),
    gust: fieldValue(row, resolution, "wind_gust", "wind_gust_max"),
  }));

  const lows = values.map((v) => v.min).filter((v) => v != null);
  const highs = values.map((v) => v.max).filter((v) => v != null);
  const avgs = values.map((v) => v.avg).filter((v) => v != null);
  const gusts = values.map((v) => v.gust).filter((v) => v != null);

  document.getElementById("stat-low").textContent =
    lows.length ? `${Math.round(Math.min(...lows))}°` : "—";
  document.getElementById("stat-high").textContent =
    highs.length ? `${Math.round(Math.max(...highs))}°` : "—";
  document.getElementById("stat-avg").textContent = avgs.length
    ? `${Math.round(avgs.reduce((a, b) => a + b, 0) / avgs.length)}°`
    : "—";
  document.getElementById("stat-gust").innerHTML = gusts.length
    ? `${formatReading(Math.max(...gusts), "wind")} <span class="tile-unit">${UNITS.wind}</span>`
    : "—";
}

async function loadHistory(start, end, resolution, label) {
  currentRange = { start, end, resolution };

  document.getElementById("range-label").textContent = label;
  const status = document.getElementById("history-status");
  status.textContent = "Loading…";

  try {
    const response = await fetch(
      `/site/history?start=${start}&end=${end}&resolution=${resolution}`
    );
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    const rows = body.observations;

    renderStats(rows, resolution);
    renderAllMetricCharts(charts, rows, resolution);

    status.textContent = rows.length
      ? `${rows.length.toLocaleString()} data points · ${RESOLUTION_NOTES[resolution]}`
      : "No data in this range yet";
  } catch (err) {
    status.textContent = err.message || "Unable to load history";
  }
}

function loadRange(rangeKey) {
  const { seconds, resolution, label } = RANGES[rangeKey];
  const end = Math.floor(Date.now() / 1000);
  const start = end - seconds;
  loadHistory(start, end, resolution, label);
}

// Longer spans would return huge raw payloads and /api/history caps raw at
// 31 days anyway, so pick a coarser resolution automatically as the custom
// range widens.
function resolutionForRangeSeconds(seconds) {
  if (seconds <= 2 * 86400) return "raw";
  if (seconds <= 90 * 86400) return "hourly";
  return "daily";
}

function formatRangeDate(date) {
  return date.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

const presetButtons = document.querySelectorAll("#range-presets button");

function setActivePreset(activeButton) {
  presetButtons.forEach((button) => button.classList.toggle("active", button === activeButton));
}

function applyCustomRange() {
  const startValue = document.getElementById("range-start").value;
  const endValue = document.getElementById("range-end").value;
  const status = document.getElementById("history-status");

  if (!startValue || !endValue) {
    status.textContent = "Pick both a start and end date";
    return;
  }

  const startDate = new Date(`${startValue}T00:00:00`);
  const endDate = new Date(`${endValue}T00:00:00`);
  const start = Math.floor(startDate.getTime() / 1000);
  // Include the entire end day by running through to the following midnight.
  const end = Math.floor(endDate.getTime() / 1000) + 86400;

  if (start >= end) {
    status.textContent = "Start date must be before end date";
    return;
  }

  setActivePreset(null);

  const label =
    startValue === endValue
      ? formatRangeDate(startDate)
      : `${formatRangeDate(startDate)} – ${formatRangeDate(endDate)}`;
  loadHistory(start, end, resolutionForRangeSeconds(end - start), label);
}

presetButtons.forEach((button) => {
  button.addEventListener("click", () => {
    setActivePreset(button);
    loadRange(button.dataset.range);
  });
});

document.getElementById("apply-custom-range").addEventListener("click", applyCustomRange);

function downloadHistory(format) {
  if (!currentRange) return;
  const { start, end, resolution } = currentRange;
  window.location.href =
    `/site/history?start=${start}&end=${end}&resolution=${resolution}` +
    `&format=${format}&download=1`;
}

document.getElementById("download-csv").addEventListener("click", () => downloadHistory("csv"));
document.getElementById("download-json").addEventListener("click", () => downloadHistory("json"));

(async () => {
  await loadCapabilities();
  buildChartGrid("chart-grid");
  loadRange("day");
})();
