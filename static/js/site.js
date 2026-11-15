const REFRESH_MS = 60_000;

// ---------------------------------------------------------------------------
// Formatting
//
// The cloud and local-UDP sources report the same fields at different
// precision (UV 3 vs 2.43, wind 2 vs 1.5 mph), and the cache holds whichever
// arrived last. Everything shown on the page goes through these so the
// display doesn't change shape from one refresh to the next.
// ---------------------------------------------------------------------------

const DASH = "—";

function formatDegrees(value) {
  return value != null ? `${Math.round(value)}°` : DASH;
}

function formatWhole(value) {
  return value != null ? `${Math.round(value)}` : DASH;
}

// "24.86" + a smaller, muted unit - keeps the number itself the loud part.
function valueWithUnit(valueText, unit) {
  return `${valueText} <span class="tile-unit">${unit}</span>`;
}

function degreesToCompass(deg) {
  if (deg === null || deg === undefined) return "";
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

function formatHourLabel(localHour) {
  const period = localHour >= 12 ? "PM" : "AM";
  const hour12 = localHour % 12 === 0 ? 12 : localHour % 12;
  return `${hour12} ${period}`;
}

function formatTimeOfDay(unixSeconds) {
  if (!unixSeconds) return DASH;
  // sunrise/sunset are genuine unix epochs (verified against a known
  // UTC offset: this station's day_start_local decodes to 06:00 UTC, i.e.
  // true local midnight at UTC-6) - not local clock digits mislabeled as
  // UTC, so let the browser apply its own real timezone offset.
  return new Date(unixSeconds * 1000).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function setText(id, text) {
  document.getElementById(id).textContent = text;
}

// ---------------------------------------------------------------------------
// Current conditions
// ---------------------------------------------------------------------------

const PRESSURE_TRENDS = { rising: "Rising ↑", falling: "Falling ↓", steady: "Steady →" };

// Standard UV index exposure categories. The level drives the meter colour;
// the label is always shown next to it so colour never carries it alone.
function uvCategory(uv) {
  if (uv < 3) return { label: "Low", level: "good" };
  if (uv < 6) return { label: "Moderate", level: "warning" };
  if (uv < 8) return { label: "High", level: "serious" };
  if (uv < 11) return { label: "Very high", level: "critical" };
  return { label: "Extreme", level: "critical" };
}

let latestCurrent = null; // last /api/current payload
let todayForecast = null; // forecast.daily[0], for the hero's high/low + sun times

// Bits of the hero that need both /api/current and /api/forecast; called
// from whichever of the two arrives.
function renderHeroForecastBits() {
  const hilo = document.getElementById("current-hilo");
  const sun = document.getElementById("stat-sun");

  if (!todayForecast) {
    hilo.textContent = "";
    sun.textContent = DASH;
    return;
  }

  hilo.textContent =
    todayForecast.air_temp_high != null && todayForecast.air_temp_low != null
      ? `High ${formatDegrees(todayForecast.air_temp_high)} · Low ${formatDegrees(todayForecast.air_temp_low)}`
      : "";

  sun.innerHTML = `
    <span title="Sunrise">${uiIcon("sunrise")}${formatTimeOfDay(todayForecast.sunrise)}</span>
    <span title="Sunset">${uiIcon("sunset")}${formatTimeOfDay(todayForecast.sunset)}</span>`;
}

function renderWind(data) {
  const needle = document.getElementById("compass-needle");

  if (data.wind_avg == null) {
    setText("stat-wind", DASH);
    setText("stat-gust", "");
    needle.toggleAttribute("hidden", true);
    return;
  }

  const speed = formatReading(data.wind_avg, "wind");
  const calm = Number(speed) === 0;
  const direction = degreesToCompass(data.wind_direction);
  document.getElementById("stat-wind").innerHTML =
    calm ? "Calm" : `${valueWithUnit(speed, UNITS.wind)} ${direction}`;
  setText(
    "stat-gust",
    data.wind_gust != null ? `Gusts ${formatReading(data.wind_gust, "wind")} ${UNITS.wind}` : ""
  );

  // No meaningful bearing when it's calm. (An SVG element has no .hidden
  // property, hence the attribute.)
  const hasBearing = !calm && data.wind_direction != null;
  needle.toggleAttribute("hidden", !hasBearing);
  if (hasBearing) needle.style.transform = `rotate(${data.wind_direction}deg)`;
}

function renderCurrent(data) {
  latestCurrent = data;

  document.getElementById("station-status").hidden = true;
  document.getElementById("current-card").hidden = false;

  applyWeatherBackground(data.icon);

  document.getElementById("current-icon").innerHTML = weatherIcon(data.icon);
  setText("current-temp", formatDegrees(data.air_temperature));
  setText("current-conditions", data.conditions || "");
  setText(
    "current-feels",
    data.feels_like != null ? `Feels like ${formatDegrees(data.feels_like)}` : ""
  );

  renderWind(data);

  const humidity = data.relative_humidity;
  setText("stat-humidity", humidity != null ? `${Math.round(humidity)}%` : DASH);
  document.getElementById("meter-humidity").style.width =
    humidity != null ? `${Math.min(100, Math.max(0, humidity))}%` : "0";
  setText(
    "stat-dew-point",
    data.dew_point != null ? `Dew point ${formatDegrees(data.dew_point)}` : ""
  );

  document.getElementById("stat-pressure").innerHTML =
    data.station_pressure != null
      ? valueWithUnit(formatReading(data.station_pressure, "pressure"), UNITS.pressure)
      : DASH;
  setText("stat-pressure-trend", PRESSURE_TRENDS[data.pressure_trend] || "");

  const uvMeter = document.getElementById("meter-uv");
  if (data.uv != null) {
    // Categorise the rounded figure, so the label always agrees with the
    // number shown next to it.
    const category = uvCategory(Math.round(data.uv));
    document.getElementById("stat-uv").innerHTML = valueWithUnit(
      Math.round(data.uv),
      category.label
    );
    uvMeter.style.width = `${Math.min(100, (data.uv / 11) * 100)}%`;
    uvMeter.dataset.level = category.level;
  } else {
    setText("stat-uv", DASH);
    uvMeter.style.width = "0";
  }
  setText(
    "stat-solar",
    data.solar_radiation != null ? `Solar ${formatWhole(data.solar_radiation)} W/m²` : ""
  );

  document.getElementById("stat-rain").innerHTML =
    data.precip_accum_local_day != null
      ? valueWithUnit(formatReading(data.precip_accum_local_day, "precip"), UNITS.precip)
      : DASH;
  setText(
    "stat-rain-yesterday",
    data.precip_accum_local_yesterday != null
      ? `Yesterday ${formatReading(data.precip_accum_local_yesterday, "precip")} ${UNITS.precip}`
      : ""
  );

  if (data.lightning_strike_last_epoch) {
    // Tempest words the message in the requested distance unit itself.
    setText(
      "stat-lightning",
      data.lightning_strike_last_distance_msg ||
        `${data.lightning_strike_last_distance} ${UNITS.distance}`
    );
    setText("stat-lightning-when", formatRelativeTime(data.lightning_strike_last_epoch));
  } else {
    setText("stat-lightning", "None");
    setText("stat-lightning-when", "No recent strikes");
  }

  setText(
    "last-updated-rest",
    data.rest_updated_at ? `Tempest cloud updated ${formatRelativeTime(data.rest_updated_at)}` : ""
  );
  // Only ever set once UDP is enabled and has received at least one obs_st -
  // stays blank (not "—") otherwise, since there's nothing wrong to report.
  setText(
    "last-updated-udp",
    data.udp_updated_at ? `Local station updated ${formatRelativeTime(data.udp_updated_at)}` : ""
  );

  renderHeroForecastBits();
  // Today's row carries a "now" marker that tracks the current temperature.
  if (latestDaily.length) renderDailyForecast(latestDaily);
}

// Shown above the dashboard only while there's something to say (loading,
// waiting, failed); hidden again as soon as a snapshot renders.
function showStatus(message) {
  const banner = document.getElementById("station-status");
  banner.textContent = message;
  banner.hidden = false;
}

async function refresh() {
  try {
    const response = await fetch("/api/current");
    if (response.status === 503) {
      showStatus("Waiting for the first observation…");
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderCurrent(await response.json());
  } catch (err) {
    showStatus("Unable to load current conditions");
  }
}

// ---------------------------------------------------------------------------
// Last-24-hours charts
// ---------------------------------------------------------------------------

const trendCharts = {}; // chart key -> Chart.js instance, reused across refreshes

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
      ? `${rows.length.toLocaleString()} observations`
      : "No data in the last 24 hours yet";
  } catch (err) {
    status.textContent = "Unable to load the last 24 hours";
  }
}

// ---------------------------------------------------------------------------
// Forecast
// ---------------------------------------------------------------------------

function dayLabel(index, dayStartLocal) {
  if (index === 0) return "Today";
  // day_start_local is a genuine unix epoch for the station's local midnight
  // (e.g. 06:00 UTC for a UTC-6 station), not local clock digits mislabeled
  // as UTC - so the browser's own local-timezone conversion is what's
  // actually correct here, not a forced "UTC" override.
  return new Date(dayStartLocal * 1000).toLocaleDateString([], {
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

let allHourly = [];
let hourlyGroups = [];
let latestDaily = [];
let selectedDayIndex = 0;

// The first tab is a rolling next-24-hours window (running on past midnight)
// rather than "what's left of today", which is nearly empty by evening.
// Every later tab is that calendar day in full.
function hoursForSelectedTab() {
  if (selectedDayIndex === 0) return allHourly.slice(0, 24);
  return hourlyGroups[selectedDayIndex] ? hourlyGroups[selectedDayIndex].hours : [];
}

function renderHourlyDayTabs(daily) {
  const container = document.getElementById("hourly-day-tabs");

  container.innerHTML = hourlyGroups
    .map((group, index) => {
      const dailyIndex = daily.findIndex((d) => d.day_num === group.dayNum);
      let label = `Day ${group.dayNum}`;
      if (index === 0) label = "Next 24h";
      else if (dailyIndex >= 0) label = dayLabel(dailyIndex, daily[dailyIndex].day_start_local);
      const activeClass = index === selectedDayIndex ? "active" : "";
      return `<button type="button" class="${activeClass}" data-day-index="${index}">${label}</button>`;
    })
    .join("");

  container.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      selectedDayIndex = Number(button.dataset.dayIndex);
      renderHourlyDayTabs(daily);
      renderHourlyStrip();
      document.getElementById("hourly-forecast").scrollLeft = 0;
    });
  });
}

const HOUR_CELL_WIDTH = 58; // px; the temperature curve is drawn to this grid
const HOUR_CURVE_HEIGHT = 92;

// Smooth line through the points (Catmull-Rom converted to cubic Béziers).
function smoothPath(points) {
  if (points.length === 0) return "";
  let d = `M${points[0][0]} ${points[0][1]}`;
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[Math.max(0, i - 1)];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[Math.min(points.length - 1, i + 2)];
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${c1[0].toFixed(1)} ${c1[1].toFixed(1)} ${c2[0].toFixed(1)} ${c2[1].toFixed(1)} ${p2[0]} ${p2[1].toFixed(1)}`;
  }
  return d;
}

function renderHourlyStrip() {
  const container = document.getElementById("hourly-forecast");
  const hours = hoursForSelectedTab();
  if (hours.length === 0) {
    container.innerHTML = "";
    return;
  }

  const temps = hours.map((hour) => hour.air_temperature).filter((t) => t != null);
  const low = Math.min(...temps);
  const high = Math.max(...temps);
  const top = 26; // room for the temperature label above the line
  const bottom = HOUR_CURVE_HEIGHT - 8;
  const yFor = (temp) =>
    high === low ? (top + bottom) / 2 : top + (1 - (temp - low) / (high - low)) * (bottom - top);

  const points = [];
  hours.forEach((hour, index) => {
    if (hour.air_temperature == null) return;
    points.push([index * HOUR_CELL_WIDTH + HOUR_CELL_WIDTH / 2, yFor(hour.air_temperature), hour]);
  });

  const line = smoothPath(points);
  const width = hours.length * HOUR_CELL_WIDTH;
  const area = points.length
    ? `${line} L${points[points.length - 1][0]} ${HOUR_CURVE_HEIGHT} L${points[0][0]} ${HOUR_CURVE_HEIGHT} Z`
    : "";
  const labels = points
    .map(
      ([x, y, hour]) =>
        `<text x="${x}" y="${(y - 9).toFixed(1)}">${Math.round(hour.air_temperature)}°</text>`
    )
    .join("");

  const cells = hours
    .map((hour) => {
      // Midnight is where the day changes, so name the new day there.
      const isDayStart = hour.local_hour === 0;
      const time = isDayStart
        ? new Date(hour.time * 1000).toLocaleDateString([], { weekday: "short" })
        : formatHourLabel(hour.local_hour);
      const precip =
        hour.precip_probability > 0 ? `${uiIcon("drop")}${hour.precip_probability}%` : "";
      return `
        <div class="hourly-cell" style="width:${HOUR_CELL_WIDTH}px" title="${escapeHtml(hour.conditions || "")}">
          ${weatherIcon(hour.icon)}
          <div class="hourly-precip">${precip}</div>
          <div class="hourly-time${isDayStart ? " day-start" : ""}">${time}</div>
        </div>`;
    })
    .join("");

  container.innerHTML = `
    <div class="hourly-track">
      <svg class="hourly-curve" width="${width}" height="${HOUR_CURVE_HEIGHT}" viewBox="0 0 ${width} ${HOUR_CURVE_HEIGHT}" aria-hidden="true">
        <path class="curve-area" d="${area}" />
        <path class="curve-line" d="${line}" />
        ${labels}
      </svg>
      <div class="hourly-cells">${cells}</div>
    </div>`;
}

function renderDailyForecast(daily) {
  const container = document.getElementById("daily-forecast");

  // Every row's bar sits on one shared scale (coldest low to warmest high
  // across the whole forecast) so the bars are comparable down the list.
  const lows = daily.map((day) => day.air_temp_low).filter((t) => t != null);
  const highs = daily.map((day) => day.air_temp_high).filter((t) => t != null);
  const scaleMin = Math.min(...lows);
  const scaleMax = Math.max(...highs);
  const percent = (temp) =>
    scaleMax === scaleMin ? 50 : ((temp - scaleMin) / (scaleMax - scaleMin)) * 100;

  container.innerHTML = daily
    .map((day, index) => {
      const precip =
        day.precip_probability != null && day.precip_probability > 0
          ? `${uiIcon("drop")}${day.precip_probability}%`
          : "";

      let bar = "";
      if (day.air_temp_low != null && day.air_temp_high != null) {
        const left = percent(day.air_temp_low);
        const width = percent(day.air_temp_high) - left;
        let now = "";
        if (index === 0 && latestCurrent && latestCurrent.air_temperature != null) {
          const position = Math.min(100, Math.max(0, percent(latestCurrent.air_temperature)));
          now = `<div class="range-now" style="left:${position.toFixed(1)}%" title="Now ${formatDegrees(latestCurrent.air_temperature)}"></div>`;
        }
        bar = `<div class="range-fill" style="left:${left.toFixed(1)}%;width:${width.toFixed(1)}%"></div>${now}`;
      }

      const sunTimes = `Sunrise ${formatTimeOfDay(day.sunrise)} · Sunset ${formatTimeOfDay(day.sunset)}`;

      return `
        <div class="daily-row" title="${sunTimes}">
          <div class="daily-day">${dayLabel(index, day.day_start_local)}</div>
          ${weatherIcon(day.icon)}
          <div class="daily-conditions">${escapeHtml(day.conditions || "")}</div>
          <div class="daily-precip">${precip}</div>
          <div class="daily-low">${formatDegrees(day.air_temp_low)}</div>
          <div class="range-track">${bar}</div>
          <div class="daily-high">${formatDegrees(day.air_temp_high)}</div>
        </div>`;
    })
    .join("");
}

async function refreshForecast() {
  const forecastColumn = document.getElementById("forecast-col");

  // Forecast needs the cloud source specifically - not just "not ready yet."
  // Drop the column entirely rather than showing it empty when it's
  // permanently unavailable (UDP-only setups).
  if (!CAPABILITIES.cloud_configured) return;

  try {
    const response = await fetch("/api/forecast");
    if (!response.ok) return; // not available yet (still starting up); leave as-is
    const body = await response.json();
    latestDaily = body.daily || [];
    todayForecast = latestDaily[0] || null;

    allHourly = body.hourly || [];
    hourlyGroups = groupHourlyByDay(allHourly);
    if (selectedDayIndex >= hourlyGroups.length) selectedDayIndex = 0;

    renderHourlyDayTabs(latestDaily);
    renderHourlyStrip();
    renderDailyForecast(latestDaily);
    renderHeroForecastBits();

    forecastColumn.hidden = false;
    document.getElementById("dashboard").classList.remove("no-forecast");
  } catch (err) {
    // leave sections as-is
  }
}

// ---------------------------------------------------------------------------
// Station health
// ---------------------------------------------------------------------------

function formatUptime(seconds) {
  if (seconds == null) return DASH;
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  if (days > 0) return `${days}d ${hours}h`;
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${hours}h ${minutes}m`;
}

// Rough thresholds based on WeatherFlow's own guidance - not exact, just a
// quick at-a-glance signal rather than a precise spec.
function batteryStatus(voltage) {
  if (voltage >= 2.6) return { label: "Good", level: "good" };
  if (voltage >= 2.4) return { label: "OK", level: "warning" };
  return { label: "Low", level: "critical" };
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

    const batteryStatusEl = document.getElementById("health-battery-status");
    if (data.device_voltage != null) {
      const status = batteryStatus(data.device_voltage);
      document.getElementById("health-battery").innerHTML = valueWithUnit(
        data.device_voltage.toFixed(2),
        "V"
      );
      batteryStatusEl.innerHTML = `<span class="status-dot" data-level="${status.level}"></span>${status.label}`;
    } else {
      setText("health-battery", DASH);
      batteryStatusEl.textContent = "";
    }

    document.getElementById("health-signal").innerHTML =
      data.device_rssi != null ? valueWithUnit(data.device_rssi, "dBm") : DASH;
    setText("health-uptime", formatUptime(data.hub_uptime));
    setText(
      "health-firmware",
      data.hub_firmware_revision != null ? `v${data.hub_firmware_revision}` : DASH
    );
  } catch (err) {
    card.hidden = true;
  }
}

(async () => {
  await loadCapabilities();

  // Rain, lightning and sun times only exist in the cloud data.
  if (!CAPABILITIES.cloud_configured) {
    document.querySelectorAll('[data-source="cloud"]').forEach((el) => (el.hidden = true));
    document.getElementById("dashboard").classList.add("no-forecast");
  }

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
