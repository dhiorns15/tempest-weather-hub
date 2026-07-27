const RANGES = {
  day: { seconds: 86400, resolution: "raw" },
  week: { seconds: 7 * 86400, resolution: "hourly" },
  month: { seconds: 30 * 86400, resolution: "hourly" },
  year: { seconds: 365 * 86400, resolution: "daily" },
};

const charts = {}; // metric key -> Chart.js instance, reused across range switches

function renderStats(rows, resolution) {
  const values = rows.map((row) => ({
    avg: fieldValue(row, resolution, "air_temperature", "air_temperature_avg"),
    min: fieldValue(row, resolution, "air_temperature", "air_temperature_min"),
    max: fieldValue(row, resolution, "air_temperature", "air_temperature_max"),
  }));

  const lows = values.map((v) => v.min).filter((v) => v != null);
  const highs = values.map((v) => v.max).filter((v) => v != null);
  const avgs = values.map((v) => v.avg).filter((v) => v != null);

  document.getElementById("stat-low").textContent =
    lows.length ? `${Math.round(Math.min(...lows))}°` : "—";
  document.getElementById("stat-high").textContent =
    highs.length ? `${Math.round(Math.max(...highs))}°` : "—";
  document.getElementById("stat-avg").textContent = avgs.length
    ? `${Math.round(avgs.reduce((a, b) => a + b, 0) / avgs.length)}°`
    : "—";
}

async function loadHistory(start, end, resolution) {
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
      ? `Showing ${rows.length} data points`
      : "No data in this range yet";
  } catch (err) {
    status.textContent = err.message || "Unable to load history";
  }
}

function loadRange(rangeKey) {
  const { seconds, resolution } = RANGES[rangeKey];
  const end = Math.floor(Date.now() / 1000);
  const start = end - seconds;
  loadHistory(start, end, resolution);
}

// Longer spans would return huge raw payloads and /api/history caps raw at
// 31 days anyway, so pick a coarser resolution automatically as the custom
// range widens.
function resolutionForRangeSeconds(seconds) {
  if (seconds <= 2 * 86400) return "raw";
  if (seconds <= 90 * 86400) return "hourly";
  return "daily";
}

function applyCustomRange() {
  const startValue = document.getElementById("range-start").value;
  const endValue = document.getElementById("range-end").value;
  const status = document.getElementById("history-status");

  if (!startValue || !endValue) {
    status.textContent = "Pick both a start and end date";
    return;
  }

  const start = Math.floor(new Date(`${startValue}T00:00:00`).getTime() / 1000);
  // Include the entire end day by running through to the following midnight.
  const end = Math.floor(new Date(`${endValue}T00:00:00`).getTime() / 1000) + 86400;

  if (start >= end) {
    status.textContent = "Start date must be before end date";
    return;
  }

  document
    .querySelectorAll(".range-selector button")
    .forEach((b) => b.classList.remove("active"));

  loadHistory(start, end, resolutionForRangeSeconds(end - start));
}

document.querySelectorAll(".range-selector button").forEach((button) => {
  button.addEventListener("click", () => {
    document
      .querySelectorAll(".range-selector button")
      .forEach((b) => b.classList.remove("active"));
    button.classList.add("active");
    loadRange(button.dataset.range);
  });
});

document.getElementById("apply-custom-range").addEventListener("click", applyCustomRange);

(async () => {
  await loadCapabilities();
  buildChartGrid("chart-grid");
  loadRange("day");
})();
