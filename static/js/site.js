const ICONS = {
  "clear-day": "☀️",
  "clear-night": "🌙",
  cloudy: "☁️",
  "partly-cloudy-day": "⛅",
  "partly-cloudy-night": "🌥️",
  foggy: "🌫️",
  "possibly-rainy-day": "🌦️",
  "possibly-rainy-night": "🌧️",
  rainy: "🌧️",
  "possibly-sleet-day": "🌨️",
  "possibly-sleet-night": "🌨️",
  sleet: "🌨️",
  "possibly-snow-day": "🌨️",
  "possibly-snow-night": "🌨️",
  snow: "❄️",
  "possibly-thunderstorm-day": "⛈️",
  "possibly-thunderstorm-night": "⛈️",
  thunderstorm: "⛈️",
  windy: "💨",
};
const DEFAULT_ICON = "🌡️";

const REFRESH_MS = 60_000;

function degreesToCompass(deg) {
  if (deg === null || deg === undefined) return "—";
  const points = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
  ];
  return points[Math.round(deg / 22.5) % 16];
}

function formatRelativeTime(unixSeconds) {
  const deltaMs = Date.now() - unixSeconds * 1000;
  const minutes = Math.round(deltaMs / 60_000);
  if (minutes < 1) return "just now";
  if (minutes === 1) return "1 minute ago";
  if (minutes < 60) return `${minutes} minutes ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return hours === 1 ? "1 hour ago" : `${hours} hours ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "1 day ago" : `${days} days ago`;
}

const PRESSURE_TREND_ARROWS = { rising: "↑", falling: "↓", steady: "→" };

function lightningSummary(data) {
  if (!data.lightning_strike_last_epoch) return "None recently";
  const distance = data.lightning_strike_last_distance_msg || `${data.lightning_strike_last_distance} mi`;
  return `${distance}, ${formatRelativeTime(data.lightning_strike_last_epoch)}`;
}

function renderCurrent(data) {
  document.getElementById("station-status").textContent = "Current conditions";

  const card = document.getElementById("current-card");
  card.hidden = false;

  applyWeatherBackground(data.icon);

  document.getElementById("current-icon").textContent =
    ICONS[data.icon] || DEFAULT_ICON;
  document.getElementById("current-temp").textContent =
    data.air_temperature != null ? `${Math.round(data.air_temperature)}°` : "—";
  document.getElementById("current-conditions").textContent = data.conditions || "";

  document.getElementById("stat-feels-like").textContent =
    data.feels_like != null ? `${Math.round(data.feels_like)}°` : "—";
  document.getElementById("stat-humidity").textContent =
    data.relative_humidity != null ? `${Math.round(data.relative_humidity)}%` : "—";
  document.getElementById("stat-dew-point").textContent =
    data.dew_point != null ? `${Math.round(data.dew_point)}°` : "—";
  document.getElementById("stat-wind").textContent =
    data.wind_avg != null
      ? `${data.wind_avg} mph ${degreesToCompass(data.wind_direction)}`
      : "—";
  document.getElementById("stat-gust").textContent =
    data.wind_gust != null ? `${data.wind_gust} mph` : "—";
  document.getElementById("stat-pressure").textContent =
    data.station_pressure != null
      ? `${data.station_pressure} inHg ${PRESSURE_TREND_ARROWS[data.pressure_trend] || ""}`
      : "—";
  document.getElementById("stat-uv").textContent =
    data.uv != null ? data.uv : "—";
  document.getElementById("stat-solar").textContent =
    data.solar_radiation != null ? `${data.solar_radiation} W/m²` : "—";
  document.getElementById("stat-lightning").textContent = lightningSummary(data);

  document.getElementById("last-updated-rest").textContent = data.rest_updated_at
    ? `Tempest cloud updated ${formatRelativeTime(data.rest_updated_at)}`
    : "";
  // Only ever set once UDP is enabled and has received at least one obs_st -
  // stays blank (not "—") otherwise, since there's nothing wrong to report.
  document.getElementById("last-updated-udp").textContent = data.udp_updated_at
    ? `Local station updated ${formatRelativeTime(data.udp_updated_at)}`
    : "";
}

async function refresh() {
  try {
    const response = await fetch("/api/current");
    if (response.status === 503) {
      document.getElementById("station-status").textContent =
        "Waiting for the first observation…";
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderCurrent(await response.json());
  } catch (err) {
    document.getElementById("station-status").textContent =
      "Unable to load current conditions";
  }
}

const trendCharts = {}; // metric key -> Chart.js instance, reused across refreshes

async function refreshTrend() {
  const status = document.getElementById("trend-status");
  const end = Math.floor(Date.now() / 1000);
  const start = end - 86400;

  try {
    const response = await fetch(`/site/history?start=${start}&end=${end}&resolution=raw`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const body = await response.json();
    const rows = body.observations;

    renderAllMetricCharts(trendCharts, rows, "raw");

    status.textContent = rows.length
      ? `${rows.length} observations over the last 24 hours`
      : "No data in the last 24 hours yet";
  } catch (err) {
    status.textContent = "Unable to load the last 24 hours";
  }
}

function formatHourLabel(localHour) {
  const period = localHour >= 12 ? "PM" : "AM";
  const hour12 = localHour % 12 === 0 ? 12 : localHour % 12;
  return `${hour12} ${period}`;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function dayLabel(index, dayStartLocal) {
  if (index === 0) return "Today";
  return new Date(dayStartLocal * 1000).toLocaleDateString([], {
    timeZone: "UTC",
    weekday: "short",
  });
}

// Forecast hours already come back day-ordered from Tempest, so a single
// pass groups them; nothing here is persisted anywhere, so once a day's
// hours scroll out of Tempest's own response (it naturally drops the past
// as it moves forward), they're just gone from the next refresh - no
// separate cleanup/"dump" step needed.
function groupHourlyByDay(hourly) {
  const groups = [];
  const indexByDayNum = new Map();

  hourly.forEach((hour) => {
    if (!indexByDayNum.has(hour.local_day)) {
      indexByDayNum.set(hour.local_day, groups.length);
      groups.push({ dayNum: hour.local_day, hours: [] });
    }
    groups[indexByDayNum.get(hour.local_day)].hours.push(hour);
  });

  return groups;
}

let hourlyGroups = [];
let selectedDayIndex = 0;

function renderHourlyDayTabs(daily) {
  const container = document.getElementById("hourly-day-tabs");

  container.innerHTML = hourlyGroups
    .map((group, index) => {
      const dailyIndex = daily.findIndex((d) => d.day_num === group.dayNum);
      const label =
        dailyIndex >= 0 ? dayLabel(dailyIndex, daily[dailyIndex].day_start_local) : `Day ${group.dayNum}`;
      const activeClass = index === selectedDayIndex ? "active" : "";
      return `<button type="button" class="${activeClass}" data-day-index="${index}">${label}</button>`;
    })
    .join("");

  container.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      selectedDayIndex = Number(button.dataset.dayIndex);
      renderHourlyDayTabs(daily);
      renderHourlyStrip();
    });
  });
}

function renderHourlyStrip() {
  const container = document.getElementById("hourly-forecast");
  const hours = hourlyGroups[selectedDayIndex] ? hourlyGroups[selectedDayIndex].hours : [];

  container.innerHTML = hours
    .map((hour) => {
      const temp = hour.air_temperature != null ? Math.round(hour.air_temperature) : "—";
      return `
        <div class="hourly-item">
          <div class="hourly-time">${formatHourLabel(hour.local_hour)}</div>
          <div class="hourly-icon">${ICONS[hour.icon] || DEFAULT_ICON}</div>
          <div class="hourly-temp">${temp}°</div>
        </div>
      `;
    })
    .join("");
}

function formatTimeOfDay(unixSeconds) {
  if (!unixSeconds) return "—";
  // Tempest encodes these as the station's local wall-clock time expressed
  // as a literal unix epoch (not shifted for the viewer's timezone) - same
  // convention as day_start_local elsewhere, so force UTC formatting here
  // too rather than letting the browser apply its own offset on top.
  return new Date(unixSeconds * 1000).toLocaleTimeString([], {
    timeZone: "UTC",
    hour: "numeric",
    minute: "2-digit",
  });
}

function renderDailyForecast(daily) {
  const container = document.getElementById("daily-forecast");

  container.innerHTML = daily
    .map((day, index) => {
      const label = dayLabel(index, day.day_start_local);
      const high = day.air_temp_high != null ? Math.round(day.air_temp_high) : "—";
      const low = day.air_temp_low != null ? Math.round(day.air_temp_low) : "—";
      const precip =
        day.precip_probability != null && day.precip_probability > 0
          ? `<span class="daily-precip">${day.precip_probability}%</span>`
          : "";

      return `
        <div class="daily-item">
          <div class="daily-day">${label}</div>
          <div class="daily-icon">${ICONS[day.icon] || DEFAULT_ICON}</div>
          <div class="daily-conditions">
            ${escapeHtml(day.conditions || "")} ${precip}
            <div class="daily-sun">☀️ ${formatTimeOfDay(day.sunrise)} — 🌙 ${formatTimeOfDay(day.sunset)}</div>
          </div>
          <div class="daily-temps">
            <span class="daily-high">${high}°</span>
            <span class="daily-low">${low}°</span>
          </div>
        </div>
      `;
    })
    .join("");
}

async function refreshForecast() {
  const hourlySection = document.getElementById("hourly-forecast-section");
  const dailySection = document.getElementById("daily-forecast-section");

  // Forecast needs the cloud source specifically - not just "not ready yet."
  // Hide both sections entirely rather than showing them empty when it's
  // permanently unavailable (UDP-only setups).
  if (!CAPABILITIES.cloud_configured) {
    hourlySection.hidden = true;
    dailySection.hidden = true;
    return;
  }

  try {
    const response = await fetch("/api/forecast");
    if (!response.ok) return; // not available yet (still starting up); leave as-is
    const body = await response.json();
    const daily = body.daily || [];

    hourlyGroups = groupHourlyByDay(body.hourly || []);
    if (selectedDayIndex >= hourlyGroups.length) selectedDayIndex = 0;

    renderHourlyDayTabs(daily);
    renderHourlyStrip();
    renderDailyForecast(daily);

    hourlySection.hidden = false;
    dailySection.hidden = false;
  } catch (err) {
    // leave sections as-is
  }
}

function formatUptime(seconds) {
  if (seconds == null) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  if (days > 0) return `${days}d ${hours}h`;
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${hours}h ${minutes}m`;
}

// Rough thresholds based on WeatherFlow's own guidance - not exact, just a
// quick at-a-glance signal rather than a precise spec.
function batteryLabel(voltage) {
  if (voltage == null) return "—";
  const status = voltage >= 2.6 ? "Good" : voltage >= 2.4 ? "OK" : "Low";
  return `${voltage.toFixed(2)}V (${status})`;
}

async function refreshStationHealth() {
  const card = document.getElementById("station-health-card");
  try {
    const response = await fetch("/api/station-health");
    if (!response.ok) {
      card.hidden = true; // UDP disabled, or no status broadcast seen yet
      return;
    }
    const data = await response.json();
    card.hidden = false;

    document.getElementById("health-battery").textContent = batteryLabel(data.device_voltage);
    document.getElementById("health-signal").textContent =
      data.device_rssi != null ? `${data.device_rssi} dBm` : "—";
    document.getElementById("health-uptime").textContent = formatUptime(data.hub_uptime);
    document.getElementById("health-firmware").textContent =
      data.hub_firmware_revision != null ? `v${data.hub_firmware_revision}` : "—";
  } catch (err) {
    card.hidden = true;
  }
}

(async () => {
  await loadCapabilities();
  buildChartGrid("chart-grid");
  refresh();
  refreshTrend();
  refreshForecast();
  refreshStationHealth();
  setInterval(refresh, REFRESH_MS);
  setInterval(refreshTrend, REFRESH_MS);
  setInterval(refreshForecast, REFRESH_MS);
  setInterval(refreshStationHealth, REFRESH_MS);
})();
